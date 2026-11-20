"""Reconciliations run from the app's screens.

Each one reuses the command-line reconciliation exactly (workbench.recon and its `orders`
subcommand), so the screens and the CLI write identical files. A reconciliation lives in
<projects>/reconciliations/<id>/:

    request.json   what was asked: kind, period, account, settings, and each input's SHA-256
    inputs/        the uploaded exports, kept unchanged
    outputs/       the CLI's files (summary.md, matches.csv, exceptions.csv, result.json, and
                   close-state.json when a month close was saved), published only when complete
    status.json    queued, running, completed, failed or interrupted

Reconciliations share the app's single worker with project runs, so one job runs at a time.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .common import WorkbenchError, file_hash, now, read_json, write_json
from .store import remove_path

KINDS = {
    "payouts": "Stripe payouts to QuickBooks deposits",
    "orders": "Store orders to payments",
}
PLATFORMS = {"shopify": "Shopify orders · Shopify Payments", "woocommerce-stripe": "WooCommerce orders · Stripe charges"}
# Input roles each kind takes, and whether each is required.
ROLES = {
    "payouts": {"payouts": True, "itemized": False, "books": True},
    "orders": {"orders": True, "transactions": True},
}
OUTPUTS = ("summary.md", "matches.csv", "refunds.csv", "exceptions.csv", "result.json", "close-state.json")
ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}")
UPLOAD = re.compile(r"[0-9a-f]{32}")


def safe_name(name):
    name = Path(str(name or "")).name
    name = re.sub(r"[^A-Za-z0-9 ._()\-]+", "_", name).strip(" .") or "export"
    if Path(name).suffix.lower() not in (".csv", ".xlsx"):
        raise WorkbenchError("Use a .csv or .xlsx export.")
    return name[-120:]


class Reconciliations:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "reconciliations"
        self.uploads = store.runtime / "recon-uploads"

    # Uploads are held in this session's working folder until a reconciliation claims them.
    def stage_upload(self, filename, temporary):
        name = safe_name(filename)
        uid = uuid.uuid4().hex
        folder = self.uploads / uid
        folder.mkdir(parents=True)
        target = folder / name
        shutil.copyfile(temporary, target)
        return {"id": uid, "name": name, "bytes": target.stat().st_size, "sha256": file_hash(target)}

    def _claim(self, uid):
        if not isinstance(uid, str) or not UPLOAD.fullmatch(uid):
            raise WorkbenchError("Upload the file again.")
        files = list((self.uploads / uid).glob("*"))
        if len(files) != 1:
            raise WorkbenchError("That upload has expired. Upload the file again.")
        return files[0]

    def path(self, rid):
        if not isinstance(rid, str) or not ID.fullmatch(rid) or not (self.root / rid / "request.json").is_file():
            raise WorkbenchError("Reconciliation not found.")
        return self.root / rid

    def closes(self, account=None):
        """Saved month closes that a later payout reconciliation can carry forward."""
        found = []
        for state in sorted(self.root.glob("*/outputs/close-state.json"), reverse=True):
            request = read_json(state.parent.parent / "request.json")
            if account is None or request.get("close_account") == account:
                found.append({"id": state.parent.parent.name, "account": request.get("close_account"),
                              "period": request.get("period"), "currency": read_json(state).get("currency")})
        return found

    def start(self, payload, synchronous=False):
        self.store.require_primary()
        kind = payload.get("kind")
        if kind not in KINDS:
            raise WorkbenchError("Choose what to reconcile.")
        request = {"kind": kind, "created": now()}
        if kind == "payouts":
            period = str(payload.get("period") or "")
            if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", period):
                raise WorkbenchError("Choose the month to reconcile.")
            try:
                window = int(payload.get("window_after", 5))
            except (TypeError, ValueError):
                window = -1
            if not 0 <= window <= 31:
                raise WorkbenchError("Allow 0 to 31 days for a deposit to post.")
            processor = str(payload.get("processor") or "stripe").strip()
            try:
                re.compile(processor)
            except re.error:
                raise WorkbenchError("The processor name is not a valid pattern.")
            account = str(payload.get("close_account") or "").strip()
            if len(account) > 80:
                raise WorkbenchError("Keep the bank account label under 80 characters.")
            prior = payload.get("prior_close") or None
            if prior:
                if not account:
                    raise WorkbenchError("Name the bank account to carry a close forward.")
                if prior not in {c["id"] for c in self.closes(account)}:
                    raise WorkbenchError("Choose a saved close for this bank account.")
            request.update(period=period, window_after=window, processor=processor, close_account=account or None, prior_close=prior)
        else:
            platform = payload.get("platform")
            if platform not in PLATFORMS:
                raise WorkbenchError("Choose the store and payment platform.")
            request.update(platform=platform, match_postcode=bool(payload.get("match_postcode")))
        files = payload.get("files") or {}
        chosen = {}
        for role, required in ROLES[kind].items():
            if files.get(role):
                chosen[role] = self._claim(files[role])
            elif required:
                raise WorkbenchError(f"Add the {role} export.")
        with self.store.lock:
            if self.store.busy:
                raise WorkbenchError("Another job is processing. Wait for it to finish before starting a reconciliation.")
            rid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            folder = self.root / rid
            (folder / "inputs").mkdir(parents=True)
            request["inputs"] = {}
            for role, upload in chosen.items():
                # Kept under its own name: the reports cite records by file name and row.
                target = folder / "inputs" / role / upload.name
                target.parent.mkdir()
                shutil.move(str(upload), target)
                remove_path(upload.parent)
                request["inputs"][role] = {"name": upload.name, "stored": f"{role}/{upload.name}", "sha256": file_hash(target)}
            write_json(folder / "status.json", {"id": rid, "state": "queued", "message": "Waiting to start"})
            write_json(folder / "request.json", request)  # written last: listing looks for this file
            self.store.busy = True
            if synchronous:
                self._execute(rid)
            else:
                self.store.worker.submit(self._execute, rid)
        return self.get(rid)

    def _arguments(self, folder, request, out):
        inputs = {role: str(folder / "inputs" / item["stored"]) for role, item in request["inputs"].items()}
        if request["kind"] == "orders":
            argv = ["orders", "--platform", request["platform"], "--orders", inputs["orders"],
                    "--transactions", inputs["transactions"], "--out", str(out)]
            return argv + (["--match-postcode"] if request["match_postcode"] else [])
        argv = ["--payouts", inputs["payouts"], "--books", inputs["books"], "--period", request["period"],
                "--out", str(out), "--window-after", str(request["window_after"]), "--processor", request["processor"]]
        if "itemized" in inputs:
            argv += ["--itemized", inputs["itemized"]]
        if request["close_account"]:
            argv += ["--close-account", request["close_account"]]
        if request["prior_close"]:
            argv += ["--prior-close", str(self.root / request["prior_close"] / "outputs" / "close-state.json")]
        return argv

    def _execute(self, rid):
        from .recon.__main__ import main as reconcile

        folder = self.root / rid
        staging = folder / "outputs.partial"
        status = {"id": rid}
        try:
            write_json(folder / "status.json", {"id": rid, "state": "running", "message": "Matching records"})
            request = read_json(folder / "request.json")
            remove_path(staging)
            printed, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
                try:
                    code = reconcile(self._arguments(folder, request, staging))
                except SystemExit as stop:  # argparse rejected an argument
                    code = stop.code if isinstance(stop.code, int) else 2
            if code not in (0, 1) or not (staging / "result.json").is_file():
                message = errors.getvalue().strip().splitlines()
                raise WorkbenchError(message[-1].removeprefix("Error: ") if message else "The reconciliation could not read these exports.")
            staging.rename(folder / "outputs")  # published only once every file is written
            status.update(state="completed", message="Reconciliation saved", outputs=sorted(p.name for p in (folder / "outputs").iterdir()))
        except Exception as exc:
            remove_path(staging)
            status.update(state="failed", message=str(exc))
        finally:
            self.store.busy = False
        write_json(folder / "status.json", status)

    def list(self):
        items = []
        for request_file in sorted(self.root.glob("*/request.json"), reverse=True):
            folder = request_file.parent
            if ID.fullmatch(folder.name):
                items.append(self._summary(folder))
        return items

    def _summary(self, folder):
        request = read_json(folder / "request.json")
        try:
            status = read_json(folder / "status.json")
        except (OSError, ValueError):
            status = {"state": "failed", "message": "This reconciliation's status could not be read. Run it again."}
        summary = {"id": folder.name, "kind": request["kind"], "title": KINDS[request["kind"]], "created": request["created"],
                   "state": status["state"], "message": status.get("message", ""),
                   "period": request.get("period"), "account": request.get("close_account"),
                   "platform": PLATFORMS.get(request.get("platform"))}
        result = folder / "outputs" / "result.json"
        if status["state"] == "completed" and result.is_file():
            data = read_json(result)
            if request["kind"] == "payouts":
                summary["headline"] = {"payouts": data["counts"]["payouts"], "total": data["tieout"]["payouts_total"],
                                       "unexplained": data["tieout"]["unexplained"], "exceptions": len(data["exceptions"])}
            else:
                states = [o["status"] for o in data["orders"]]
                summary["headline"] = {"orders": len(states), "matched": states.count("matched"),
                                       "review": states.count("review"), "exceptions": len(data["exceptions"])}
        return summary

    def get(self, rid):
        folder = self.path(rid)
        detail = self._summary(folder)
        detail["request"] = read_json(folder / "request.json")
        result = folder / "outputs" / "result.json"
        if detail["state"] == "completed" and result.is_file():
            detail["result"] = read_json(result)
            detail["files"] = [name for name in OUTPUTS if (folder / "outputs" / name).is_file()]
            close = folder / "outputs" / "close-state.json"
            if close.is_file():
                detail["currency"] = read_json(close).get("currency")
        return detail

    def file(self, rid, name):
        if name not in OUTPUTS:
            raise WorkbenchError("Choose a reconciliation output.")
        path = self.path(rid) / "outputs" / name
        if not path.is_file():
            raise WorkbenchError("That file is not part of this reconciliation.")
        return path
