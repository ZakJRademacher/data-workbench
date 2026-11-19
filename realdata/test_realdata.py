"""Run real public datasets through the workbench and compare every result with the oracle.

Sample mode slices each dataset (every push); full mode uses every row and also
checks published facts about the datasets (nightly).
"""
import csv
import copy
from decimal import Decimal

import duckdb
import pytest

from realdata.conftest import FULL
from realdata import inputs, oracle
from workbench.common import WorkbenchError


def table(recipe, name):
    return next(t for t in recipe["tables"] if t["name"] == name)


def field(recipe, tname, fname):
    return next(f for f in table(recipe, tname)["fields"] if f["name"] == fname)


def tables(manifest):
    return {t["name"]: t for t in manifest["tables"]}


def connect(out):
    return duckdb.connect(str(out / "data.duckdb"), read_only=True)


def read_csv(path, encoding="utf-8-sig"):
    with open(path, encoding=encoding, newline="") as stream:
        return list(csv.DictReader(stream))


# --- Olist: five related tables, a lateness rule, and an order-level combined view ---------

def configure_olist(recipe):
    for t in recipe["tables"]:  # orders.csv in samples, olist_orders_dataset.csv in full runs
        t["name"] = next(s for s in ("items", "payments", "customers", "products", "orders") if s in t["name"])
    keys = {"orders": ["order_id"], "items": ["order_id", "order_item_id"], "payments": ["order_id", "payment_sequential"],
            "customers": ["customer_id"], "products": ["product_id"]}
    for t in recipe["tables"]:
        t["key"] = keys[t["name"]]
    table(recipe, "orders")["checks"] = [{"left": "order_delivered_customer_date", "op": "<=",
                                          "right": "order_estimated_delivery_date", "message": "Delivered after estimate"}]
    recipe["views"] = [{"name": "order_summary", "base": "orders", "joins": [
        {"table": "customers", "left_on": ["customer_id"], "right_on": ["customer_id"], "how": "left", "approved": True},
        {"table": "items", "left_on": ["order_id"], "right_on": ["order_id"], "how": "left", "approved": True,
         "aggregations": [{"op": "count", "field": "order_item_id", "name": "item_count"},
                          {"op": "sum", "field": "price", "name": "items_price"},
                          {"op": "sum", "field": "freight_value", "name": "items_freight"}]},
        {"table": "payments", "left_on": ["order_id"], "right_on": ["order_id"], "how": "left", "approved": True,
         "aggregations": [{"op": "sum", "field": "payment_value", "name": "paid"}]}]}]


@pytest.fixture(scope="module")
def olist(cache, work, run):
    files = inputs.olist(cache, work, FULL)
    pid, _, manifest, out = run("olist", [files[k] for k in ("orders", "items", "payments", "customers", "products")], configure_olist)
    return pid, manifest, out, oracle.olist(files)


def test_olist_row_counts_and_totals(olist):
    _, manifest, _, expected = olist
    t = tables(manifest)
    assert {n: t[n]["input"] for n in expected["rows"]} == expected["rows"]
    assert Decimal(t["items"]["totals"]["price"]["sum"]) == expected["price_total"]
    assert Decimal(t["items"]["totals"]["freight_value"]["sum"]) == expected["freight_total"]
    assert Decimal(t["payments"]["totals"]["payment_value"]["sum"]) == expected["payment_total"]
    if FULL:
        assert expected["rows"] == oracle.FULL_FACTS["olist_rows"]
        assert expected["payment_total"] == oracle.FULL_FACTS["olist_payment_total"]


def test_olist_lateness_rule_holds_exactly_the_late_orders(olist):
    _, manifest, out, expected = olist
    with connect(out) as con:
        held = dict(con.execute("SELECT order_id, __issues FROM result_orders WHERE __status='review'").fetchall())
    assert set(held) == expected["late"]
    assert set(held.values()) <= {"Delivered after estimate"}
    if FULL:
        assert len(expected["late"]) == oracle.FULL_FACTS["olist_late_orders"]


def test_olist_order_view_matches_independent_rollup(olist):
    _, manifest, out, expected = olist
    view = manifest["views"][0]
    assert view["before"] == view["after"] == len(expected["summary"])
    with connect(out) as con:
        got = {r[0]: {"item_count": r[1], "items_price": r[2], "items_freight": r[3], "paid": r[4]} for r in con.execute(
            'SELECT order_id, "items__item_count", "items__items_price", "items__items_freight", "payments__paid" FROM order_summary').fetchall()}
    norm = lambda d: {k: None if v is None else Decimal(str(v)) for k, v in d.items()}
    mismatched = [oid for oid, exp in expected["summary"].items() if norm(got.get(oid, {})) != norm(exp)]
    assert not mismatched, mismatched[:5]


def test_olist_cleaned_text_equals_source(olist):
    _, _, out, expected = olist
    exported = read_csv(out / "orders.csv")
    assert exported
    for row in exported:
        source = expected["orders_by_id"][row["order_id"]]
        assert {k: row[k] for k in source} == source


def test_olist_blocks_a_join_that_would_double_count(olist, store):
    pid, *_ = olist
    recipe = store.get(pid)["recipe"]
    naive = copy.deepcopy(recipe)
    naive["views"] = [{"name": "naive", "base": "orders", "joins": [
        {"table": "items", "left_on": ["order_id"], "right_on": ["order_id"], "how": "left", "approved": True}]}]
    store.set_recipe(pid, naive)
    try:
        with pytest.raises(WorkbenchError, match="repeated lookup keys"):
            store.preview_views(pid)
    finally:
        store.set_recipe(pid, recipe)


# --- NYC flights 2013: R-style NA markers, float noise, five-way join ----------------------

def configure_flights(recipe):
    for t in recipe["tables"]:
        for f in t["fields"]:
            f["missing"] = ["NA"]
    for n in ("distance", "arr_delay", "dep_delay", "air_time"):
        field(recipe, "flights", n).update(type="integer", inferred=False)
    for n in ("temp", "dewp", "humid", "precip", "pressure", "visib"):
        field(recipe, "weather", n)["type"] = "decimal"
    field(recipe, "weather", "wind_dir")["type"] = "integer"
    for n in ("wind_speed", "wind_gust"):  # 15-digit float noise cannot be an exact decimal
        field(recipe, "weather", n)["type"] = "text"
    for n in ("lat", "lon"):
        field(recipe, "airports", n)["type"] = "text"
    for name, key in {"planes": ["tailnum"], "airports": ["faa"], "airlines": ["carrier"],
                      "weather": ["origin", "time_hour"]}.items():
        table(recipe, name)["key"] = key
    table(recipe, "flights")["duplicate_policy"] = "keep_all"
    recipe["views"] = [{"name": "flight_detail", "base": "flights", "joins": [
        {"table": "planes", "left_on": ["tailnum"], "right_on": ["tailnum"], "how": "left", "approved": True},
        {"table": "airports", "left_on": ["dest"], "right_on": ["faa"], "how": "left", "approved": True},
        {"table": "airlines", "left_on": ["carrier"], "right_on": ["carrier"], "how": "left", "approved": True},
        {"table": "weather", "left_on": ["origin", "time_hour"], "right_on": ["origin", "time_hour"], "how": "left", "approved": True}]}]


@pytest.fixture(scope="module")
def flights(cache, work, run):
    files = inputs.flights(cache, work, FULL)
    _, _, manifest, out = run("nycflights13", [files[n] for n in ("flights", "planes", "airports", "airlines", "weather")], configure_flights)
    return manifest, out, oracle.flights(files)


def test_flights_all_rows_accepted_and_totals_exact(flights):
    manifest, _, expected = flights
    t = tables(manifest)["flights"]
    assert t["input"] == t["accepted"] == expected["rows"]
    for name, (total, populated) in expected["totals"].items():
        assert t["totals"][name] == {"sum": str(total), "populated": populated}, name
    if FULL:
        assert expected["rows"] == oracle.FULL_FACTS["flights_rows"]


def test_flights_weather_holds_only_unparseable_readings(flights):
    _, out, expected = flights
    with connect(out) as con:
        held = set(con.execute("SELECT origin, time_hour FROM result_weather WHERE __status='review'").fetchall())
    assert held == expected["weather_held"]


def test_flights_join_gaps_match_independent_counts(flights):
    manifest, _, expected = flights
    view = manifest["views"][0]
    assert view["before"] == view["after"] == expected["rows"]
    gaps = {j["lookup"]: j["unmatched_base_rows"] for j in view["joins"]}
    assert gaps == {"planes": expected["without_plane"], "airports": expected["without_destination_airport"],
                    "airlines": 0, "weather": expected["without_weather"]}
    if FULL:
        assert expected["without_plane"] == oracle.FULL_FACTS["flights_without_plane"]
        assert expected["without_destination_airport"] == oracle.FULL_FACTS["flights_without_destination_airport"]


# --- UCI Online Retail: cancellations, exact repeat lines ---------------------------------

def configure_retail(recipe):
    t = recipe["tables"][0]
    field(recipe, t["name"], "invoiceno")["type"] = "text"  # cancellations look like C536379
    t["duplicate_policy"] = "drop_exact"


def test_retail_exact_repeats_and_totals(cache, work, run):
    path = inputs.retail(cache, work, FULL)
    _, _, manifest, _ = run("online_retail", [path], configure_retail)
    expected = oracle.retail(path)
    t = manifest["tables"][0]
    assert (t["input"], t["accepted"], t["review"], t["excluded"]) == (
        expected["rows"], expected["rows"] - expected["repeat_copies"], 0, expected["repeat_copies"])
    assert t["totals"]["quantity"]["sum"] == str(expected["quantity_total"])
    assert Decimal(t["totals"]["unitprice"]["sum"]) == expected["price_total"]
    if FULL:
        assert expected["rows"] == oracle.FULL_FACTS["retail_rows"]
        assert expected["repeat_copies"] == oracle.FULL_FACTS["retail_exact_repeat_copies"]


# --- Excel exports with real date cells (regression: every row used to go to review) -------

def configure_excel(recipe):
    t = recipe["tables"][0]
    field(recipe, t["name"], "invoiceno")["type"] = "text"
    field(recipe, t["name"], "invoicedate").update(type="date", date_format="%Y-%m-%d")
    t["duplicate_policy"] = "keep_all"


def test_excel_date_cells_become_dates(cache, work, run):
    paths = inputs.retail_excel(cache, work, FULL)
    _, _, manifest, out = run("excel_dates", paths, configure_excel)
    expected = oracle.retail_excel(paths)
    t = manifest["tables"][0]
    assert (t["accepted"], t["review"]) == (sum(expected["accepted"].values()), expected["held"])
    with connect(out) as con:
        got = con.execute(f'SELECT invoiceno, stockcode, invoicedate FROM "{t["name"]}"').fetchall()
    from collections import Counter
    assert Counter(got) == expected["accepted"]


# --- OurAirports: CSV export must not alter text (regression: 17,851 coordinates changed) ---

def test_airport_export_preserves_every_text_value(cache, run):
    path = cache / "airports/airport-codes.csv"
    _, profiles, manifest, out = run("airport_codes", [path], lambda r: r["tables"][0].update(key=["ident"]))
    expected = oracle.airports(path)
    t = manifest["tables"][0]
    assert t["input"] == expected["rows"] and t["accepted"] == expected["unique_idents"]
    fields = profiles[path.name]["fields"]
    text_fields = {f["name"]: h for f, h in zip(fields, profiles[path.name]["headers"]) if f["type"] == "text"}
    changed = [(row["ident"], name) for row in read_csv(out / f"{t['name']}.csv")
               for name, header in text_fields.items() if row[name] != expected["by_ident"][row["ident"]][header]]
    assert not changed, changed[:5]
    assert not manifest["csv_escaped_cells"]


# --- Windows-1252 file whose first accent is past the sniffing sample (regression) ---------

def test_cp1252_detected_beyond_sample(cache, work, run):
    path = inputs.us_airports_cp1252(cache, work)
    _, profiles, manifest, out = run("cp1252_airports", [path])
    expected = oracle.cp1252_airports(path)
    assert profiles[path.name]["settings"]["encoding"] == "cp1252"
    assert not manifest["file_issues"]
    t = manifest["tables"][0]
    assert t["input"] == t["accepted"] == expected["rows"]
    assert sorted(r["name"] for r in read_csv(out / f"{t['name']}.csv")) == expected["names"]
