import copy
import csv
import json
from pathlib import Path

import duckdb
import pytest
from openpyxl import Workbook, load_workbook

from workbench.common import WorkbenchError, file_hash, read_json, write_json
from workbench.demo import EXPECTED, create_demo
from workbench.store import ProjectStore
from conftest import add_csv, completed


def records(store, pid, result, table, status="all"):
    data = store.preview(pid, result["id"], table, status)
    return [dict(zip(data["columns"], row)) for row in data["rows"]]


def test_demo_reconciles_independent_controls(store):
    project = create_demo(store)
    result = completed(store, project["id"])
    m = result["manifest"]
    assert m["complete_inputs"] is True
    assert m["input_rows"] == 20 and m["review_rows"] == 6
    for actual in m["tables"]:
        expected = EXPECTED[actual["name"]]
        for key in ("input", "accepted", "review", "excluded"):
            assert actual[key] == expected[key]
        assert actual["input"] == actual["accepted"] + actual["review"] + actual["excluded"]
    assert m["tables"][1]["totals"]["amount"]["sum"] == EXPECTED["orders"]["amount_total"]
    assert m["tables"][2]["totals"]["paid_amount"]["sum"] == EXPECTED["payments"]["paid_amount_total"]
    customers = records(store, project["id"], result, "customers", "accepted")
    assert customers[0]["customer_id"] == "001" and customers[0]["name"] == "Mira Chen"
    assert customers[1]["region"] == "East"
    assert any(s["base"] == "orders" and s["lookup"] == "customers" for s in m["relationship_suggestions"])
    assert all(file_hash(store.run_dir(project["id"], result["id"]) / "outputs" / name) == sha for name, sha in m["output_hashes"].items())


def test_review_corrections_exclusions_refresh_and_safe_joins(store):
    project = create_demo(store)
    pid = project["id"]
    first = completed(store, pid)
    for row in records(store, pid, first, "orders", "review"):
        if row["order_id"] == "O102":
            store.set_decision(pid, row["__record_id"], {"order_date": "2026-01-05"}, False)
        if row["order_id"] == "O104":
            store.set_decision(pid, row["__record_id"], {"amount": "30.00"}, False)
    for row in records(store, pid, first, "customers", "review"):
        if row["name"] == "June B.":
            store.set_decision(pid, row["__record_id"], {}, True)
    recipe = store.get(pid)["recipe"]
    next(t for t in recipe["tables"] if t["name"] == "orders")["duplicate_policy"] = "drop_exact"
    store.set_recipe(pid, recipe)
    second = completed(store, pid)
    assert second["manifest"]["review_rows"] == 0
    orders = next(t for t in second["manifest"]["tables"] if t["name"] == "orders")
    assert orders["accepted"] == 7 and orders["excluded"] == 1
    assert orders["totals"]["amount"]["sum"] == "625.975000000000"
    assert store.start_run(pid, synchronous=True)["id"] == second["id"]
    recipe["views"] = [{"name": "orders_combined", "base": "orders", "joins": [
        {"table": "customers", "left_on": ["customer_id"], "right_on": ["customer_id"], "how": "left", "aggregations": [], "approved": False},
        {"table": "payments", "left_on": ["order_id"], "right_on": ["order_id"], "how": "left", "aggregations": [], "approved": False}]}]
    store.set_recipe(pid, recipe)
    with pytest.raises(WorkbenchError, match="approve"):
        store.start_run(pid)
    with pytest.raises(WorkbenchError, match="repeated lookup keys"):
        store.preview_views(pid)
    recipe["views"][0]["joins"][1]["aggregations"] = [{"op": "sum", "field": "paid_amount", "name": "paid_total"}]
    store.set_recipe(pid, recipe)
    preview = store.preview_views(pid)
    assert preview[0]["before"] == preview[0]["after"] == 7
    assert preview[0]["joins"][0]["unmatched_base_rows"] == 1
    assert preview[0]["joins"][1]["unmatched_lookup_rows"] == 1
    for j in recipe["views"][0]["joins"]:
        j["approved"] = True
    store.set_recipe(pid, recipe)
    third = completed(store, pid)
    assert third["manifest"]["views"][0]["after"] == 7
    joined = records(store, pid, third, "orders_combined", "accepted")
    o100 = next(r for r in joined if r["order_id"] == "O100")
    assert str(o100["payments__paid_total"]) == "125.500000000000"
    assert store.run_status(pid, first["id"])["stale"] is True


def test_composite_keys_and_keep_all(store, tmp_path):
    pid = store.create("Composite")['id']
    add_csv(store, pid, tmp_path, "rows.csv", 'shop_id,line_id,amount\n001,01,1.2500\n001,02,2\n001,02,2\n')
    recipe = store.set_recipe(pid)
    t = recipe["tables"][0]
    t["key"] = ["shop_id", "line_id"]
    t["duplicate_policy"] = "keep_all"
    store.set_recipe(pid, recipe)
    result = completed(store, pid)
    assert result["manifest"]["tables"][0]["accepted"] == 3
    assert result["manifest"]["tables"][0]["totals"]["amount"]["sum"] == "5.250000000000"


def test_encoding_quoted_records_numbers_and_precision(store, tmp_path):
    pid = store.create("Formats")["id"]
    add_csv(store, pid, tmp_path, "rows.csv", 'id;label;amount;date\n001;"café\nquoted";"1.234,50";31/01/2026\n002;other;"1,2,3";01/02/2026\n003;tiny;"0,1234567890123";02/02/2026\n004;empty;;03/02/2026\n', "cp1252")
    recipe = store.set_recipe(pid)
    t = recipe["tables"][0]
    for f in t["fields"]:
        if f["name"] == "amount":
            f.update(type="decimal", decimal_separator=",", thousands_separator=".")
        if f["name"] == "date":
            f.update(type="date", date_format="%d/%m/%Y")
    store.set_recipe(pid, recipe)
    result = completed(store, pid)
    assert result["manifest"]["tables"][0]["accepted"] == 2
    assert result["manifest"]["tables"][0]["review"] == 2
    assert result["manifest"]["tables"][0]["totals"]["amount"] == {"sum": "1234.500000000000", "populated": 1}
    clean = records(store, pid, result, "rows", "accepted")
    assert clean[0]["id"] == "001" and clean[0]["label"] == 'café\nquoted'
    assert str(clean[0]["date"]) == "2026-01-31"


def test_malformed_file_not_silently_partial(store, tmp_path):
    pid = store.create("Bad source")["id"]
    text = 'id,value\n' + ''.join(f'{i},ok\n' for i in range(250)) + '999,wrong,width\n'
    add_csv(store, pid, tmp_path, "rows.csv", text)
    store.set_recipe(pid)
    result = completed(store, pid)
    assert result["manifest"]["status"] == "incomplete"
    assert result["manifest"]["input_rows"] == 0
    assert result["manifest"]["file_issues"][0]["parsed_rows"] is None


def test_excel_sheets_misplaced_headers_and_missing_formula_cache(store, tmp_path):
    path = tmp_path / "sheet.xlsx"
    book = Workbook()
    book.active.title = "Instructions"
    book.active.append(["Choose Records"])
    ws = book.create_sheet("Records")
    ws.append(["Export report"])
    ws.append(["id", "amount"])
    ws.append(["001", 12.5])
    ws.append(["002", "=1+1"])
    book.save(path)
    pid = store.create("Excel")["id"]
    source = store.add_source(pid, path.name, path)
    configured = store.configure_source(pid, source["id"], {"sheet": "Records", "header_row": 2})
    assert configured["settings"]["header_row"] == 2
    store.set_recipe(pid)
    result = completed(store, pid)
    assert result["manifest"]["tables"][0]["review"] == 1
    assert 'Missing cached formula result' in records(store, pid, result, "sheet", "review")[0]["__issues"]
    output = store.run_dir(pid, result["id"]) / "outputs" / "Review.xlsx"
    review_book = load_workbook(output, read_only=True, data_only=True)
    assert review_book.sheetnames[0] == "Project summary"
    assert review_book.properties.creator == "Zak Rademacher"
    review_book.close()


def test_output_tampering_rebuild_and_restart(store, tmp_path):
    pid = create_demo(store)["id"]
    first = completed(store, pid)
    target = store.run_dir(pid, first["id"]) / "outputs" / "orders.csv"
    target.write_text('tampered', encoding='utf-8')
    second = completed(store, pid)
    assert second["id"] != first["id"]
    fake = store.run_dir(pid, "deadbeef")
    write_json(fake / "status.json", {"id": "deadbeef", "state": "running", "fingerprint": "", "progress": 10, "message": "in progress"})
    reopened = ProjectStore(store.root)
    assert reopened.run_status(pid, "deadbeef")["state"] == "interrupted"
    assert reopened.get(pid)["latest_run"] == second["id"]
    reopened.worker.shutdown()


def test_failure_does_not_publish_manifest(store, monkeypatch):
    pid = create_demo(store)["id"]
    def fail(*args, **kwargs):
        raise OSError("Simulated publication interruption")
    monkeypatch.setattr('workbench.store.process', fail)
    result = store.start_run(pid, synchronous=True)
    assert result["state"] == "failed" and "manifest" not in result
    assert not (store.run_dir(pid, result["id"]) / "outputs" / "manifest.json").exists()


def test_schema_drift_and_explicit_omissions(store, tmp_path):
    pid = store.create("Drift")["id"]
    source = add_csv(store, pid, tmp_path, "rows.csv", 'id,value\n001,old\n')
    recipe = store.set_recipe(pid)
    path = tmp_path / "new.csv"
    path.write_text('id,value,extra\n001,new,keep me\n')
    store.add_source(pid, "rows.csv", path, source["id"])
    with pytest.raises(WorkbenchError, match="every source column"):
        store.set_recipe(pid, recipe)
    recipe["tables"][0]["sources"][0]["ignored_columns"] = ["extra"]
    store.set_recipe(pid, recipe)
    assert completed(store, pid)["manifest"]["tables"][0]["accepted"] == 1


def test_csv_formula_text_inert_and_exact_raw_audit(store, tmp_path):
    pid = store.create("Formula text")["id"]
    add_csv(store, pid, tmp_path, "rows.csv", 'id,label,amount\n001,=1+1,-12.5000\n')
    store.set_recipe(pid)
    result = completed(store, pid)
    output = store.run_dir(pid, result["id"]) / "outputs"
    with (output / "rows.csv").open(encoding="utf-8-sig", newline='') as stream:
        row = next(csv.DictReader(stream))
    assert row["label"] == "'=1+1" and row["amount"] == "-12.500000000000"
    raw = records(store, pid, result, "rows")[0]["__raw"]
    assert json.loads(raw)["label"] == "=1+1"
