"""Regressions found by running real public datasets through the workbench."""
import csv
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from conftest import add_csv, completed
from workbench.common import WorkbenchError, csv_safe_text


def run_outputs(store, pid):
    result = completed(store, pid)
    return result, store.run_dir(pid, result["id"]) / "outputs"


def test_excel_date_cells_can_be_typed_as_dates(store, tmp_path):
    # pandas.to_excel and Excel itself store dates as date cells, which openpyxl
    # reads back as midnight datetimes. Choosing Date must convert them.
    path = tmp_path / "orders.xlsx"
    book = Workbook()
    ws = book.active
    ws.append(["order_id", "order_date"])
    ws.append(["A1", date(2026, 1, 3)])
    ws.append(["A2", datetime(2026, 1, 4, 0, 0)])
    ws.append(["A3", "03/01/2026"])            # text in the chosen day-first format
    ws.append(["A4", datetime(2026, 1, 5, 8, 26)])  # a real time of day is not a date
    book.save(path)
    pid = store.create("Excel dates")["id"]
    store.add_source(pid, path.name, path)
    recipe = store.set_recipe(pid)
    field = next(f for f in recipe["tables"][0]["fields"] if f["name"] == "order_date")
    field.update(type="date", date_format="%d/%m/%Y")
    store.set_recipe(pid, recipe)
    result, _ = run_outputs(store, pid)
    rows = store.preview(pid, result["id"], "orders", "all")
    by_id = {r[rows["columns"].index("order_id")]: r for r in rows["rows"]}
    value = rows["columns"].index("order_date")
    status = rows["columns"].index("__status")
    assert by_id["A1"][value] == date(2026, 1, 3)
    assert by_id["A2"][value] == date(2026, 1, 4)
    assert by_id["A3"][value] == date(2026, 1, 3)
    assert by_id["A4"][status] == "review"


def test_midnight_iso_timestamps_in_csv_become_dates(store, tmp_path):
    pid = store.create("Midnight timestamps")["id"]
    add_csv(store, pid, tmp_path, "est.csv",
            "id,due\n1,2017-10-18 00:00:00\n2,2017-10-19T00:00:00\n3,2017-10-20 10:56:33\n4,2017-10-21\n")
    recipe = store.set_recipe(pid)
    recipe["tables"][0]["fields"][1]["type"] = "date"
    store.set_recipe(pid, recipe)
    result, _ = run_outputs(store, pid)
    summary = result["manifest"]["tables"][0]
    assert (summary["accepted"], summary["review"]) == (3, 1)


def test_csv_export_keeps_signed_data_but_escapes_formulas(store, tmp_path):
    pid = store.create("Signed text")["id"]
    add_csv(store, pid, tmp_path, "places.csv",
            'id,coordinates,phone,note\n'
            '1,"-67.566941, -68.126993",+44 20 7946 0000,=1+1\n'
            '2,"40.070985, -74.933689",-,"+cmd|\' /C calc\'!A0"\n'
            '3,"-6.06, 141.09",(555) 010-0199,@SUM(A1)\n'
            '4,"1, 2",123,"-2+3+cmd|\' /C calc\'!A0"\n')
    store.set_recipe(pid)
    result, out = run_outputs(store, pid)
    rows = list(csv.DictReader(open(out / "places.csv", encoding="utf-8-sig")))
    assert [r["coordinates"] for r in rows] == ["-67.566941, -68.126993", "40.070985, -74.933689", "-6.06, 141.09", "1, 2"]
    assert [r["phone"] for r in rows] == ["+44 20 7946 0000", "-", "(555) 010-0199", "123"]
    assert all(r["note"].startswith("'") for r in rows)
    assert result["manifest"]["csv_escaped_cells"]["places.csv"] == 4


def test_csv_safe_text_rules():
    for kept in ["-12.5", "+1 (555) 010-0199", "-67.5, -68.1", "2026-01-03", "-", "plain", 5, None]:
        assert csv_safe_text(kept) == kept
    for escaped in ["=HYPERLINK(\"x\")", "@SUM(A1)", "+cmd|' /C calc'!A0", "-2+3+cmd|' /C calc'!A0", "\t=1", " =1", "-A1"]:
        assert csv_safe_text(escaped) == "'" + escaped


def test_cp1252_detected_when_first_accent_is_past_the_sample(store, tmp_path):
    # A large mostly-ASCII export whose first accented name appears after 128 KB.
    lines = ["id,name,city"] + [f"{i},Airport {i},Springfield" for i in range(6000)] + ["6000,Piñon Canyon Airport,La Cañada"]
    content = "\n".join(lines) + "\n"
    assert content.index("ñ") > 128 * 1024
    pid = store.create("Late cp1252")["id"]
    profile = add_csv(store, pid, tmp_path, "airports.csv", content, encoding="cp1252")
    assert profile["settings"]["encoding"] == "cp1252"
    store.set_recipe(pid)
    result, out = run_outputs(store, pid)
    assert result["manifest"]["tables"][0]["accepted"] == 6001
    rows = list(csv.DictReader(open(out / "airports.csv", encoding="utf-8-sig")))
    assert rows[-1]["name"] == "Piñon Canyon Airport"


def test_wrong_encoding_messages_name_the_fix(store, tmp_path):
    pid = store.create("Forced UTF-8")["id"]
    # Error inside the inspected sample: refused when the setting is chosen.
    profile = add_csv(store, pid, tmp_path, "names.csv", "id,name\n1,José\n", encoding="cp1252")
    with pytest.raises(WorkbenchError, match="Windows-1252"):
        store.configure_source(pid, profile["id"], {**profile["settings"], "encoding": "utf-8-sig"})
    # Error past the inspected sample: the run records an actionable file issue.
    content = "id,name\n" + "".join(f"{i},Name {i}\n" for i in range(20000)) + "20000,José\n"
    late = add_csv(store, pid, tmp_path, "late.csv", content, encoding="cp1252")
    store.configure_source(pid, late["id"], {**late["settings"], "encoding": "utf-8-sig"})
    recipe = store.set_recipe(pid)
    result = completed(store, pid)
    issue = next(i for i in result["manifest"]["file_issues"] if i["filename"] == "late.csv")["reason"]
    assert "Windows-1252" in issue and "No rows from this file were accepted" in issue
