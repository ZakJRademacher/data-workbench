"""AI recipe suggestions, tested with a fake model client (no network, no API key)."""
import json
from types import SimpleNamespace

import pytest

from conftest import add_csv, completed
from workbench.ai_suggest import SAMPLE_ROWS, apply_proposal, describe, suggest
from workbench.common import WorkbenchError


class FakeClient:
    def __init__(self, proposal, stop_reason="end_turn"):
        self.proposal, self.stop_reason, self.requests = proposal, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.proposal))])


def column(heading, type="text", missing=(), date_format="", reason="r"):
    return {"heading": heading, "type": type, "missing": list(missing), "date_format": date_format, "reason": reason}


def table(source, columns, key=(), policy="flag", name="t"):
    return {"source_file": source, "table_name": name, "key": list(key), "key_reason": "r",
            "duplicate_policy": policy, "columns": columns}


@pytest.fixture
def orders_and_lines(store, tmp_path):
    pid = store.create("AI suggestions")["id"]
    add_csv(store, pid, tmp_path, "orders.csv",
            "Order ID,Placed,Total\nA1,2026-01-03,10.50\nA2,2026-01-04,NA\nA3,2026-01-05,7.25\n")
    add_csv(store, pid, tmp_path, "lines.csv",
            "Order ID,Line,Qty\nA1,1,2\nA1,2,1.5\nA2,1,3\n")
    return pid


PROPOSAL = {
    "tables": [
        table("orders.csv", [column("Order ID"), column("Placed", "date", date_format="%Y-%m-%d"),
                             column("Total", "decimal", missing=["NA"])], key=["Order ID"], name="orders"),
        table("lines.csv", [column("Order ID"), column("Line", "integer"), column("Qty", "integer")],
              key=["Order ID", "Line"], name="order_lines"),
    ],
    "joins": [{"base_file": "orders.csv", "lookup_file": "lines.csv", "base_columns": ["Order ID"],
               "lookup_columns": ["Order ID"], "reason": "lines per order",
               "aggregations": [{"op": "sum", "column": "Qty", "name": "units"}]}],
}


def test_model_sees_only_headings_and_a_bounded_sample(store, tmp_path):
    pid = store.create("Privacy")["id"]
    rows = "".join(f"{i},{'x' * 200}\n" for i in range(150))
    add_csv(store, pid, tmp_path, "big.csv", "id,note\n" + rows)
    sent = json.loads(describe(store.get(pid)["sources"]))
    assert sent["headings"] == ["id", "note"]
    assert len(sent["sample_rows"]) == SAMPLE_ROWS
    assert all(len(v) <= 81 for row in sent["sample_rows"] for v in row)
    assert set(sent) == {"source_file", "headings", "sample_rows"}


def test_proposal_is_applied_but_joins_wait_for_approval(store, orders_and_lines):
    sources = store.get(orders_and_lines)["sources"]
    result = apply_proposal(sources, PROPOSAL)
    recipe = result["recipe"]
    orders = next(t for t in recipe["tables"] if t["name"] == "orders")
    fields = {f["name"]: f for f in orders["fields"]}
    assert (fields["placed"]["type"], fields["total"]["type"], fields["total"]["missing"]) == ("date", "decimal", ["NA"])
    assert orders["key"] == ["order_id"]
    assert [t["name"] for t in recipe["tables"]] == ["orders", "order_lines"]
    join = recipe["views"][0]["joins"][0]
    assert join["approved"] is False and join["aggregations"][0] == {"op": "sum", "field": "qty", "name": "units"}
    assert not result["rejected"]
    store.set_recipe(orders_and_lines, recipe)  # the saved-recipe validator accepts it too
    with pytest.raises(WorkbenchError, match="approve"):
        store.start_run(orders_and_lines, synchronous=True)


def test_invalid_suggestions_are_rejected_individually(store, orders_and_lines):
    sources = store.get(orders_and_lines)["sources"]
    bad = {"tables": [table("orders.csv", [column("Order ID"), column("Nope", "integer")], key=["Missing column"]),
                      table("ghost.csv", [])],
           "joins": [{"base_file": "orders.csv", "lookup_file": "lines.csv", "base_columns": ["Placed"],
                      "lookup_columns": ["Nope"], "aggregations": [], "reason": "r"}]}
    result = apply_proposal(sources, bad)
    assert {r["where"] for r in result["rejected"]} >= {"orders.Nope", "ghost.csv", "orders.csv → lines.csv"}
    assert any(r["where"] == "orders" for r in result["rejected"])  # key on a missing column
    assert not result["recipe"].get("views")


def test_a_wrong_guess_goes_to_review_not_into_the_numbers(store, orders_and_lines):
    client = FakeClient(PROPOSAL)
    result = suggest(store.get(orders_and_lines)["sources"], client=client)
    assert client.requests[0]["model"] == "claude-opus-5-5"
    recipe = result["recipe"]
    for view in recipe["views"]:
        for join in view["joins"]:
            join["approved"] = True  # the analyst approves after previewing
    store.set_recipe(orders_and_lines, recipe)
    manifest = completed(store, orders_and_lines)["manifest"]
    lines = next(t for t in manifest["tables"] if t["name"] == "order_lines")
    # "Qty" was guessed as a whole number; the 1.5 line is held for review, not rounded.
    assert (lines["accepted"], lines["review"]) == (2, 1)
    assert lines["totals"]["qty"] == {"sum": "5", "populated": 2}
    orders = next(t for t in manifest["tables"] if t["name"] == "orders")
    assert orders["totals"]["total"] == {"sum": "17.750000000000", "populated": 2}


def test_refusal_is_reported_not_parsed(store, orders_and_lines):
    with pytest.raises(WorkbenchError, match="declined"):
        suggest(store.get(orders_and_lines)["sources"], client=FakeClient({}, stop_reason="refusal"))
