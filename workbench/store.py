from __future__ import annotations

import copy
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

from .common import (MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES, VERSION, WorkbenchError,
                     digest, file_hash, now, q, read_json, runtime_root, write_json)
from .engine import build_view, process
from .ingest import inspect
from .recipe import starter_recipe, validate, migrate


class ProjectStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workbench")
        self.busy = False
        self.runtime = runtime_root() / digest(str(self.root))[:16]
        self.engine_build = digest({p.name: file_hash(p) for p in Path(__file__).parent.glob('*.py')})
        self.runtime.mkdir(parents=True, exist_ok=True)
        for project in self.root.glob("*/project.json"):
            for status in project.parent.glob("runs/*/status.json"):
                record = read_json(status)
                if record["state"] in ("queued", "running"):
                    record.update(state="interrupted", message="The previous app session ended. Run again to rebuild; no completed package was published.")
                    write_json(status, record)

    def path(self, project_id):
        if not isinstance(project_id, str) or len(project_id) != 32 or any(c not in "0123456789abcdef" for c in project_id):
            raise WorkbenchError("Unknown project identifier.")
        path = self.root / project_id
        if not (path / "project.json").exists():
            raise WorkbenchError("Project not found.")
        return path

    def create(self, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise WorkbenchError("Give the project a name of 1–120 characters.")
        pid = uuid.uuid4().hex
        project = {"id": pid, "name": name.strip(), "created": now(), "sources": [], "recipe": None,
                   "decisions": {}, "latest_run": None}
        write_json(self.root / pid / "project.json", project)
        return project

    def list(self):
        return sorted([read_json(p) for p in self.root.glob("*/project.json")], key=lambda p: p["created"], reverse=True)

    def get(self, pid):
        project = read_json(self.path(pid) / "project.json")
        if project.get('recipe') is not None:
            project['recipe'] = migrate(project['recipe'])
        return project

    def save(self, project):
        write_json(self.path(project["id"]) / "project.json", project)

    def add_source(self, pid, filename, temporary, replace_id=None):
        with self.lock:
            project = self.get(pid)
            filename = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
            if not filename or len(filename) > 180:
                raise WorkbenchError("Use a filename of 1–180 characters.")
            previous = next((s for s in project["sources"] if s["id"] == replace_id), None)
            if replace_id and not previous:
                raise WorkbenchError("Choose an existing source to replace.")
            if not previous and len(project["sources"]) >= MAX_FILES:
                raise WorkbenchError(f"A project supports up to {MAX_FILES} source files.")
            size = Path(temporary).stat().st_size
            if size > MAX_FILE_BYTES or size + sum(s["bytes"] for s in project["sources"] if s["id"] != replace_id) > MAX_TOTAL_BYTES:
                raise WorkbenchError("Limit: 50 MB per file and 250 MB combined. Split larger inputs before adding them.")
            sid = previous["id"] if previous else uuid.uuid4().hex
            profile = inspect(temporary, filename, sid, previous["settings"] if previous else None)
            sha = file_hash(temporary)
            stored_name = sha + Path(filename).suffix.lower()
            raw = self.path(pid) / "raw" / sid
            raw.mkdir(parents=True, exist_ok=True)
            if not (raw / stored_name).exists():
                shutil.copyfile(temporary, raw / stored_name)
            profile.update(sha256=sha, bytes=size, stored_name=stored_name)
            project["sources"] = [profile if s["id"] == sid else s for s in project["sources"]] if previous else project["sources"] + [profile]
            self.save(project)
            return profile

    def configure_source(self, pid, sid, settings):
        with self.lock:
            project = self.get(pid)
            source = next((s for s in project["sources"] if s["id"] == sid), None)
            if not source:
                raise WorkbenchError("Source not found.")
            profile = inspect(self.path(pid) / "raw" / sid / source["stored_name"], source["filename"], sid, settings)
            profile.update({k: source[k] for k in ("sha256", "bytes", "stored_name")})
            project["sources"] = [profile if s["id"] == sid else s for s in project["sources"]]
            self.save(project)
            return profile

    def set_recipe(self, pid, recipe=None):
        with self.lock:
            project = self.get(pid)
            recipe = starter_recipe(project["sources"]) if recipe is None else recipe
            recipe = validate(recipe, project["sources"])
            project["recipe"] = recipe
            self.save(project)
            return recipe

    def set_decision(self, pid, record_id, corrections, exclude):
        if not isinstance(corrections, dict) or not isinstance(exclude, bool):
            raise WorkbenchError("A review decision needs a corrections object and a true/false exclusion value.")
        with self.lock:
            project = self.get(pid)
            if not project["latest_run"]:
                raise WorkbenchError("Run the project before recording decisions.")
            status = self.run_status(pid, project["latest_run"])
            if not status.get("manifest"):
                raise WorkbenchError("No completed run is available for review.")
            with duckdb.connect(str(self.run_dir(pid, project["latest_run"]) / "outputs" / "data.duckdb"), read_only=True) as con:
                found = None
                snapshot = read_json(self.run_dir(pid, project["latest_run"]) / "input-snapshot.json")
                for table in snapshot["recipe"]["tables"]:
                    row = con.execute(f"SELECT __record_id FROM {q('result_' + table['name'])} WHERE __record_id=?", [record_id]).fetchone()
                    if row:
                        found = table
                        break
            if not found or not set(corrections).issubset({f["name"] for f in found["fields"]}):
                raise WorkbenchError("The record or correction field is not present in this run.")
            if not all(isinstance(v, str) for v in corrections.values()):
                raise WorkbenchError("Correction values must be text; the saved field rules validate them on rerun.")
            project["decisions"][record_id] = {"corrections": corrections, "exclude": bool(exclude), "recorded": now()}
            self.save(project)

    def fingerprint(self, project):
        return digest({"engine": VERSION, "engine_build": self.engine_build, "recipe": project["recipe"], "sources": [{k: s[k] for k in ("id", "sha256", "settings", "filename")} for s in project["sources"]],
                       "decisions": {k: {"corrections": v["corrections"], "exclude": v["exclude"]} for k, v in project["decisions"].items()}})

    def run_dir(self, pid, rid):
        if not isinstance(rid, str) or not all(c in "0123456789abcdef-" for c in rid) or len(rid) > 60 or not rid:
            raise WorkbenchError("Unknown run identifier.")
        return self.path(pid) / "runs" / rid

    def run_status(self, pid, rid):
        path = self.run_dir(pid, rid)
        if not (path / "status.json").exists():
            raise WorkbenchError("Run not found.")
        status = read_json(path / "status.json")
        if (path / "outputs" / "manifest.json").exists():
            status["manifest"] = read_json(path / "outputs" / "manifest.json")
        status["stale"] = status.get("fingerprint") != self.fingerprint(self.get(pid))
        return status

    def start_run(self, pid, synchronous=False):
        with self.lock:
            if self.busy:
                raise WorkbenchError("Another job is processing. Wait for it to finish before starting a new run.")
            project = copy.deepcopy(self.get(pid))
            if project["recipe"] is None:
                raise WorkbenchError("Save a recipe before running.")
            project['recipe'] = validate(project["recipe"], project["sources"])
            for view in project["recipe"].get("views", []):
                if any(not j.get("approved", False) for j in view.get("joins", [])):
                    raise WorkbenchError("Preview and approve every proposed relationship before running combined views.")
            fingerprint = self.fingerprint(project)
            for source in project["sources"]:
                if file_hash(self.path(pid) / 'raw' / source['id'] / source['stored_name']) != source['sha256']:
                    raise WorkbenchError("Saved source bytes changed. Replace the affected source before running again.")
            for previous in self.path(pid).glob("runs/*/outputs/manifest.json"):
                manifest = read_json(previous)
                if manifest.get("fingerprint") == fingerprint and all((previous.parent / p).is_file() and file_hash(previous.parent / p) == h for p, h in manifest["output_hashes"].items()):
                    project["latest_run"] = previous.parent.parent.name
                    self.save(project)
                    return self.run_status(pid, project["latest_run"])
            rid = fingerprint[:16] + "-" + uuid.uuid4().hex[:8]
            run = self.run_dir(pid, rid)
            write_json(run / "status.json", {"id": rid, "state": "queued", "message": "Waiting to start", "progress": 0, "fingerprint": fingerprint})
            project["latest_run"] = rid
            self.save(project)
            self.busy = True
            if synchronous:
                self._execute(project, rid, fingerprint)
            else:
                self.worker.submit(self._execute, project, rid, fingerprint)
            return self.run_status(pid, rid)

    def _execute(self, project, rid, fingerprint):
        pid = project["id"]
        run = self.run_dir(pid, rid)
        runtime = self.runtime / rid
        def progress(message, percent):
            write_json(run / "status.json", {"id": rid, "state": "running", "message": message, "progress": percent, "fingerprint": fingerprint})
        try:
            progress("Preparing immutable run snapshot", 1)
            write_json(run / "input-snapshot.json", project)
            manifest = process(project["recipe"], project["sources"], self.path(pid) / "raw", project["decisions"], runtime, run / "outputs", progress)
            manifest.update(id=rid, project_id=pid, project_name=project["name"], fingerprint=fingerprint, engine_build=self.engine_build)
            manifest["output_hashes"] = {p.name: file_hash(p) for p in (run / "outputs").iterdir() if p.is_file() and p.name != "manifest.json"}
            write_json(run / "outputs" / "manifest.json", manifest)
            write_json(run / "status.json", {"id": rid, "state": "completed", "message": "Project package saved", "progress": 100, "fingerprint": fingerprint})
        except Exception as exc:
            write_json(run / "status.json", {"id": rid, "state": "failed", "message": str(exc), "progress": 0, "fingerprint": fingerprint})
        finally:
            self.busy = False

    def preview(self, pid, rid, table, disposition="accepted", offset=0, limit=100):
        status = self.run_status(pid, rid)
        if not status.get("manifest"):
            raise WorkbenchError("This run has no completed output.")
        snapshot = read_json(self.run_dir(pid, rid) / "input-snapshot.json")
        names = {t["name"] for t in snapshot["recipe"]["tables"]}
        views = {v["name"] for v in snapshot["recipe"].get("views", [])}
        if table not in names | views or disposition not in ("accepted", "review", "excluded", "all"):
            raise WorkbenchError("Choose an existing table and disposition.")
        with duckdb.connect(str(self.run_dir(pid, rid) / "outputs" / "data.duckdb"), read_only=True) as con:
            if table in views:
                query, params = f"SELECT * FROM {q(table)}", []
            else:
                query, params = f"SELECT * FROM {q('result_' + table)}", []
                if disposition != "all":
                    query += " WHERE __status=?"
                    params.append(disposition)
            count = con.execute(f"SELECT count(*) FROM ({query})", params).fetchone()[0]
            cursor = con.execute(query + " ORDER BY __source_name,__row::BIGINT,__record_id LIMIT ? OFFSET ?", params + [limit, offset])
            return {"columns": [d[0] for d in cursor.description], "rows": cursor.fetchall(), "total": count, "offset": offset, "stale": status["stale"]}

    def preview_views(self, pid):
        project = self.get(pid)
        if not project["latest_run"]:
            raise WorkbenchError("Run the clean tables first, then preview the proposed joins.")
        status = self.run_status(pid, project["latest_run"])
        if not status.get("manifest"):
            raise WorkbenchError("Run the clean tables first.")
        snapshot = read_json(self.run_dir(pid, project["latest_run"]) / "input-snapshot.json")
        current, old = copy.deepcopy(project), copy.deepcopy(snapshot)
        current["recipe"]["views"] = []
        old["recipe"]["views"] = []
        if self.fingerprint(current) != self.fingerprint(old):
            raise WorkbenchError("Inputs, cleaning rules, or review decisions changed. Run the tables again before previewing joins.")
        validate(project["recipe"], project["sources"])
        database = self.run_dir(pid, project["latest_run"]) / "outputs" / "data.duckdb"
        # Preview works against a disposable in-memory attachment; saved outputs remain untouched.
        with duckdb.connect() as con:
            from .common import lit
            con.execute(f"ATTACH {lit(str(database))} AS snapshot (READ_ONLY)")
            con.execute("USE snapshot")
            return [build_view(con, project["recipe"], v, materialize=False) for v in project["recipe"].get("views", [])]
