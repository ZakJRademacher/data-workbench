import json
import urllib.request

from fastapi.testclient import TestClient

from workbench import desktop
from workbench.app import create_app
from workbench.common import read_json, write_json
from workbench.demo import create_demo
from workbench.store import ProjectStore


def test_a_second_session_on_the_same_projects_never_relabels_runs_still_processing(store):
    pid = create_demo(store)["id"]
    live = store.run_dir(pid, "deadbeef")
    write_json(live / "status.json", {"id": "deadbeef", "state": "running", "fingerprint": "", "progress": 10, "message": "in progress"})
    assert store.primary
    second = ProjectStore(store.root)
    try:
        assert not second.primary
        assert read_json(live / "status.json")["state"] == "running"
    finally:
        second.close()
    store.close()
    third = ProjectStore(store.root)
    try:
        assert third.primary, "the lock is released when the first session closes"
        assert read_json(live / "status.json")["state"] == "interrupted"
    finally:
        third.close()


def test_closing_twice_is_harmless(store):
    store.close()
    store.close()


def test_only_the_desktop_key_brings_the_window_forward(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    app = create_app(tmp_path / "projects")
    try:
        client = TestClient(app)
        assert client.post("/api/desktop/focus").status_code == 403, "not a desktop session"
        shown = []
        app.state.desktop_key = "secret-key"
        app.state.desktop_focus = lambda: shown.append(True)
        assert client.post("/api/desktop/focus", headers={"X-Workbench-Desktop": "wrong"}).status_code == 403
        assert client.get("/api/desktop/focus", headers={"X-Workbench-Desktop": "secret-key"}).status_code == 403
        assert client.post("/api/desktop/focus", headers={"X-Workbench-Desktop": "secret-key"}).status_code == 204
        assert shown == [True]
    finally:
        app.state.store.close()


def test_the_server_thread_serves_on_a_free_port_and_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    app = create_app(tmp_path / "projects")
    server = desktop.Server(app, desktop.free_socket())
    try:
        server.start()
        assert server.port not in (0, 8810)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/api/session", timeout=5) as reply:
            assert json.load(reply)["project_root"] == str(app.state.store.root)
    finally:
        server.stop()
        app.state.store.close()
    assert not server.thread.is_alive()


def test_check_mode_starts_answers_and_cleans_up(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "projects"
    assert desktop.main(["--projects", str(root), "--check"]) == 0
    assert "answered at http://127.0.0.1:" in capsys.readouterr().out
    assert not (root / desktop.INSTANCE).exists()
    assert (tmp_path / "appdata" / "DataWorkbench" / "logs" / "desktop.log").is_file()
    reopened = ProjectStore(root)
    assert reopened.primary, "the check run released the projects folder"
    reopened.close()
