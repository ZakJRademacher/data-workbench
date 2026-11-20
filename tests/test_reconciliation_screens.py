import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench.app import create_app
from workbench.common import WorkbenchError, file_hash, read_json, write_json
from workbench.reconciliations import Reconciliations
from workbench.recon.__main__ import main as cli
from workbench.store import ProjectStore

ROOT = Path(__file__).resolve().parents[1]
WOO_ORDERS = 'order_number,order_date,order_total,order_currency,billing_email\n512,2026-10-01 11:00:00,51.00,USD,buyer@example.test\n'
WOO_CHARGES = ('reporting_category,created_utc,gross,currency,balance_transaction_id,charge_id,payment_metadata[order_id]\n'
               'charge,2026-10-01 15:00:00,51.00,USD,txn_charge,ch_512,512\n')


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    """The built-in fictional Stripe and QuickBooks exports."""
    folder = tmp_path_factory.mktemp("synthetic")
    subprocess.run([sys.executable, "-B", "-m", "workbench.recon.synth", "--out", str(folder)], cwd=ROOT, check=True, capture_output=True)
    return folder


def upload(recon, path):
    return recon.stage_upload(Path(path).name, path)["id"]


def payouts_request(recon, synthetic, **extra):
    files = {"payouts": upload(recon, synthetic / "stripe_payouts.csv"), "itemized": upload(recon, synthetic / "stripe_itemized.csv"),
             "books": upload(recon, synthetic / "quickbooks.csv")}
    return {"kind": "payouts", "period": "2026-10", "files": files, **extra}


def test_the_screens_write_exactly_what_the_command_line_writes(store, synthetic, tmp_path):
    recon = Reconciliations(store)
    done = recon.start(payouts_request(recon, synthetic), synchronous=True)
    assert done["state"] == "completed", done
    assert done["headline"]["payouts"] == 22 and done["headline"]["unexplained"] == "0.00"
    folder = recon.path(done["id"])
    cli(["--payouts", str(synthetic / "stripe_payouts.csv"), "--itemized", str(synthetic / "stripe_itemized.csv"),
         "--books", str(synthetic / "quickbooks.csv"), "--period", "2026-10", "--out", str(tmp_path / "cli")])
    for name in ("matches.csv", "exceptions.csv"):
        assert (folder / "outputs" / name).read_bytes() == (tmp_path / "cli" / name).read_bytes(), name
    screen, command = read_json(folder / "outputs" / "result.json"), read_json(tmp_path / "cli" / "result.json")
    assert screen["tieout"] == command["tieout"] and screen["matches"] == command["matches"]


def test_inputs_are_kept_unchanged_with_their_hashes_and_nothing_partial_is_published(store, synthetic):
    recon = Reconciliations(store)
    done = recon.start(payouts_request(recon, synthetic, close_account="Checking 1234"), synchronous=True)
    request = read_json(recon.path(done["id"]) / "request.json")
    assert request["inputs"]["books"]["sha256"] == file_hash(synthetic / "quickbooks.csv")
    assert request["inputs"]["books"]["name"] == "quickbooks.csv" and request["close_account"] == "Checking 1234"
    assert file_hash(recon.path(done["id"]) / "inputs" / "payouts" / "stripe_payouts.csv") == file_hash(synthetic / "stripe_payouts.csv")
    assert not (recon.path(done["id"]) / "outputs.partial").exists()
    detail = recon.get(done["id"])
    assert detail["currency"] == "USD" and "close-state.json" in detail["files"]
    assert list(recon.uploads.iterdir()) == [], "claimed uploads leave the working folder"


def test_a_saved_close_can_be_carried_into_the_next_month(store, synthetic):
    recon = Reconciliations(store)
    october = recon.start(payouts_request(recon, synthetic, close_account="Checking 1234"), synchronous=True)
    assert [c["id"] for c in recon.closes("Checking 1234")] == [october["id"]]
    assert recon.closes("Savings 9") == []
    with pytest.raises(WorkbenchError, match="saved close for this bank account"):
        recon.start(payouts_request(recon, synthetic, period="2026-11", close_account="Savings 9", prior_close=october["id"]))
    november = recon.start(payouts_request(recon, synthetic, period="2026-11", close_account="Checking 1234",
                                           prior_close=october["id"]), synchronous=True)
    assert november["state"] == "completed", november


def test_store_orders_reconcile_from_the_screens(store, tmp_path):
    recon = Reconciliations(store)
    (tmp_path / "orders.csv").write_text(WOO_ORDERS, encoding="utf-8")
    (tmp_path / "charges.csv").write_text(WOO_CHARGES, encoding="utf-8")
    done = recon.start({"kind": "orders", "platform": "woocommerce-stripe",
                        "files": {"orders": upload(recon, tmp_path / "orders.csv"), "transactions": upload(recon, tmp_path / "charges.csv")}},
                       synchronous=True)
    assert done["state"] == "completed", done
    assert done["headline"] == {"orders": 1, "matched": 1, "review": 0, "exceptions": 0}
    assert recon.file(done["id"], "matches.csv").read_text(encoding="utf-8").splitlines()[1].startswith("512,")


def test_an_unreadable_export_fails_with_the_reason_and_publishes_nothing(store, tmp_path):
    recon = Reconciliations(store)
    (tmp_path / "orders.csv").write_text("not,the,right,columns\n1,2,3,4\n", encoding="utf-8")
    (tmp_path / "charges.csv").write_text(WOO_CHARGES, encoding="utf-8")
    done = recon.start({"kind": "orders", "platform": "woocommerce-stripe",
                        "files": {"orders": upload(recon, tmp_path / "orders.csv"), "transactions": upload(recon, tmp_path / "charges.csv")}},
                       synchronous=True)
    assert done["state"] == "failed" and "orders.csv" in done["message"], done
    assert not (recon.path(done["id"]) / "outputs").exists()
    assert not store.busy


@pytest.mark.parametrize("payload,message", [
    ({"kind": "nothing"}, "what to reconcile"),
    ({"kind": "payouts", "period": "2026-13"}, "month"),
    ({"kind": "payouts", "period": "2026-10", "window_after": 90}, "0 to 31"),
    ({"kind": "payouts", "period": "2026-10", "processor": "("}, "pattern"),
    ({"kind": "payouts", "period": "2026-10", "prior_close": "20261101-000000-deadbeef"}, "Name the bank account"),
    ({"kind": "payouts", "period": "2026-10", "files": {}}, "payouts export"),
    ({"kind": "payouts", "period": "2026-10", "files": {"payouts": "../../etc"}}, "Upload the file again"),
    ({"kind": "orders", "platform": "amazon"}, "store and payment platform"),
])
def test_bad_requests_are_explained(store, payload, message):
    with pytest.raises(WorkbenchError, match=message):
        Reconciliations(store).start(payload)


def test_only_csv_or_excel_exports_are_accepted(store, tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    with pytest.raises(WorkbenchError, match=".csv or .xlsx"):
        Reconciliations(store).stage_upload("notes.txt", tmp_path / "notes.txt")


def test_a_reconciliation_waits_for_a_project_run_and_unfinished_ones_are_interrupted_on_restart(store, synthetic):
    recon = Reconciliations(store)
    store.busy = True
    with pytest.raises(WorkbenchError, match="Another job"):
        recon.start(payouts_request(recon, synthetic))
    store.busy = False
    folder = recon.root / "20261101-000000-deadbeef"
    write_json(folder / "request.json", {"kind": "orders", "created": "x", "platform": "shopify", "inputs": {}})
    write_json(folder / "status.json", {"id": folder.name, "state": "running", "message": "Matching records"})
    store.close()
    restarted = ProjectStore(store.root)
    try:
        assert read_json(folder / "status.json")["state"] == "interrupted"
    finally:
        restarted.close()


def test_the_api_runs_a_reconciliation_end_to_end(tmp_path, monkeypatch, synthetic):
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    app = create_app(tmp_path / "projects")
    try:
        client = TestClient(app)
        headers = {"x-workbench-token": client.get("/api/session").json()["token"]}
        files = {}
        for role, name in (("payouts", "stripe_payouts.csv"), ("books", "quickbooks.csv")):
            with open(synthetic / name, "rb") as handle:
                reply = client.post("/api/reconciliations/uploads", files={"file": (name, handle, "text/csv")}, headers=headers)
            assert reply.status_code == 201, reply.text
            files[role] = reply.json()["id"]
        started = client.post("/api/reconciliations", json={"kind": "payouts", "period": "2026-10", "files": files}, headers=headers)
        assert started.status_code == 201, started.text
        rid = started.json()["id"]
        app.state.store.worker.shutdown(wait=True)  # let the queued job finish
        detail = client.get(f"/api/reconciliations/{rid}").json()
        assert detail["state"] == "completed" and "summary.md" in detail["files"]
        summary = client.get(f"/api/reconciliations/{rid}/files/summary.md")
        assert summary.status_code == 200 and "Payout reconciliation" in summary.text
        assert client.get(f"/api/reconciliations/{rid}/files/request.json").status_code == 400
        assert client.get("/api/reconciliations/..%2F..%2Fsecret").status_code in (400, 404)
        assert [item["id"] for item in client.get("/api/reconciliations").json()["items"]] == [rid]
    finally:
        app.state.store.close()
