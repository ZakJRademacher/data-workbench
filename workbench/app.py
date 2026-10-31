from __future__ import annotations

import argparse
import json
import os
import secrets
import tempfile
import threading
import time
import webbrowser
import zipfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .common import MAX_FILE_BYTES, ROOT, VERSION, WorkbenchError, dumps, file_hash
from .demo import create_demo
from .store import ProjectStore


def create_app(project_root=None):
    store = ProjectStore(project_root or os.environ.get("DATA_WORKBENCH_PROJECTS", ROOT / "projects"))
    app = FastAPI(title="Data Workbench", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.token = secrets.token_urlsafe(32)

    def response(value, status=200):
        return Response(dumps(value), media_type="application/json", status_code=status)

    @app.exception_handler(WorkbenchError)
    async def handle_error(request, error):
        return response({"error": str(error)}, 400)

    @app.exception_handler(ValueError)
    async def handle_value_error(request, error):
        return response({"error": str(error)}, 400)

    @app.middleware("http")
    async def protect(request, call_next):
        host = request.headers.get("host", "")
        if host.split(":")[0] not in ("127.0.0.1", "localhost", "testserver"):
            return response({"error": "Localhost requests only."}, 403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("x-workbench-token") != app.state.token:
                return response({"error": "Reopen the app to refresh its local session."}, 403)
            origin = request.headers.get("origin")
            if origin and origin not in (f"http://{host}", f"https://{host}"):
                return response({"error": "Cross-origin requests are not accepted."}, 403)
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
        result.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/")
    def index():
        return HTMLResponse((ROOT / "web" / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/session")
    def session():
        return response({"token": app.state.token, "version": VERSION, "project_root": str(store.root)})

    @app.get("/api/projects")
    def projects():
        return response([{k: p[k] for k in ("id", "name", "created", "latest_run")} for p in store.list()])

    @app.post("/api/projects")
    async def new_project(request: Request):
        return response(store.create((await request.json()).get("name", "")), 201)

    @app.post("/api/demo")
    def demo():
        return response(create_demo(store), 201)

    @app.get("/api/projects/{pid}")
    def project(pid: str):
        return response(store.get(pid))

    @app.post("/api/projects/{pid}/sources")
    async def sources(pid: str, request: Request):
        # Bound the complete request before multipart parsing can spill uploads to disk.
        declared = request.headers.get("content-length")
        if declared and int(declared) > MAX_FILE_BYTES * 2:
            raise WorkbenchError("Upload one file at a time, up to 50 MB.")
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body:
            size = 0
            async for block in request.stream():
                size += len(block)
                if size > MAX_FILE_BYTES + 1024 * 1024:
                    raise WorkbenchError("Upload one file at a time, up to 50 MB.")
                body.write(block)
            body.seek(0)
            async def replay():
                while chunk := body.read(1024 * 1024):
                    yield chunk
            from starlette.formparsers import MultiPartParser
            form = await MultiPartParser(request.headers, replay(), max_files=1, max_fields=1).parse()
            upload = form.get("file")
            if not upload or not hasattr(upload, "file"):
                raise WorkbenchError("Choose a CSV or XLSX file.")
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=Path(upload.filename or "").suffix) as temporary:
                    temp_path = Path(temporary.name)
                    while block := upload.file.read(1024 * 1024):
                        temporary.write(block)
                return response(store.add_source(pid, upload.filename, temp_path, form.get("replace_id")), 201)
            finally:
                await form.close()
                if "temp_path" in locals():
                    temp_path.unlink(missing_ok=True)

    @app.put("/api/projects/{pid}/sources/{sid}")
    async def configure(pid: str, sid: str, request: Request):
        return response(store.configure_source(pid, sid, await request.json()))

    @app.put("/api/projects/{pid}/recipe")
    async def recipe(pid: str, request: Request):
        return response(store.set_recipe(pid, await request.json()))

    @app.post("/api/projects/{pid}/suggest-recipe")
    def suggest(pid: str):
        return response(store.set_recipe(pid))

    @app.post("/api/projects/{pid}/runs")
    def run(pid: str):
        return response(store.start_run(pid), 202)

    @app.get("/api/projects/{pid}/runs/{rid}")
    def run_status(pid: str, rid: str):
        return response(store.run_status(pid, rid))

    @app.get("/api/projects/{pid}/runs/{rid}/rows")
    def rows(pid: str, rid: str, table: str, disposition: str = "accepted", offset: int = 0, limit: int = 100):
        if offset < 0 or not 1 <= limit <= 200:
            raise WorkbenchError("Use a nonnegative offset and page size between 1 and 200.")
        return response(store.preview(pid, rid, table, disposition, offset, limit))

    @app.put("/api/projects/{pid}/decisions/{record_id}")
    async def decision(pid: str, record_id: str, request: Request):
        value = await request.json()
        store.set_decision(pid, record_id, value.get("corrections", {}), value.get("exclude", False))
        return response({"saved": True})

    @app.post("/api/projects/{pid}/join-preview")
    def preview_join(pid: str):
        return response(store.preview_views(pid))

    def output_dir(pid, rid):
        status = store.run_status(pid, rid)
        if not status.get("manifest"):
            raise WorkbenchError("This run has no published package. Run it again after resolving the error.")
        root = store.run_dir(pid, rid) / "outputs"
        return root, status["manifest"]

    @app.get("/api/projects/{pid}/runs/{rid}/files/{filename}")
    def download(pid: str, rid: str, filename: str):
        root, manifest = output_dir(pid, rid)
        if filename not in set(manifest["output_hashes"]) | {"manifest.json"}:
            raise WorkbenchError("Output file not found.")
        path = root / filename
        if not path.is_file() or (filename != "manifest.json" and file_hash(path) != manifest["output_hashes"][filename]):
            raise WorkbenchError("This output changed after publication. Rerun to rebuild the package.")
        return FileResponse(path, filename=filename)

    @app.get("/api/projects/{pid}/runs/{rid}/package")
    def package(pid: str, rid: str):
        root, manifest = output_dir(pid, rid)
        archive = store.runtime / (rid + "-" + secrets.token_hex(4) + ".zip")
        for filename, sha in manifest["output_hashes"].items():
            if not (root / filename).is_file() or file_hash(root / filename) != sha:
                raise WorkbenchError("An output changed after publication. Rerun before exporting.")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            for filename in [*manifest["output_hashes"], "manifest.json"]:
                zipped.write(root / filename, filename)
        return FileResponse(archive, filename="Data-Workbench-" + rid + ".zip")

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app


def main():
    parser = argparse.ArgumentParser(description="Data Workbench · local data preparation")
    parser.add_argument("--port", type=int, default=8810)
    parser.add_argument("--projects", type=Path, default=ROOT / "projects")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    app = create_app(args.projects)
    if args.open_browser:
        def open_when_ready():
            import urllib.request
            for _ in range(40):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/session", timeout=1)
                    webbrowser.open(f"http://127.0.0.1:{args.port}")
                    return
                except OSError:
                    time.sleep(0.25)
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
