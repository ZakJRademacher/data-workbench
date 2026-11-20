import json
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from workbench import desktop
from workbench.app import allowed_host, create_app


@pytest.mark.parametrize("host,port,ok", [
    ("127.0.0.1:8810", 8810, True),
    ("localhost:8810", 8810, True),
    ("127.0.0.1:8811", 8810, False),       # another local server's address
    ("attacker.example:8810", 8810, False),  # DNS rebinding keeps the attacker's name
    ("127.0.0.1.attacker.example:8810", 8810, False),
    ("127.0.0.1", 8810, False),
    ("", 8810, False),
    ("testserver", None, True),
])
def test_only_this_exact_loopback_address_is_served(host, port, ok):
    assert allowed_host(host, port) is ok


def test_requests_from_other_sites_are_refused_even_for_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    app = create_app(tmp_path / "projects")
    try:
        client = TestClient(app)
        for site in ("cross-site", "same-site"):
            assert client.get("/api/session", headers={"sec-fetch-site": site}).status_code == 403
            assert client.get("/api/projects", headers={"sec-fetch-site": site}).status_code == 403
        for site in ("same-origin", "none"):
            assert client.get("/api/projects", headers={"sec-fetch-site": site}).status_code == 200
        assert client.get("/api/projects").status_code == 200, "tools that send no fetch metadata still work"
    finally:
        app.state.store.close()


def test_the_real_server_rejects_a_rebound_host_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    app = create_app(tmp_path / "projects")
    server = desktop.Server(app, desktop.free_socket())
    try:
        server.start()
        base = f"http://127.0.0.1:{server.port}/api/session"
        with urllib.request.urlopen(base, timeout=5) as reply:
            assert "token" in json.load(reply)
        for host in (f"attacker.example:{server.port}", f"127.0.0.1:{server.port + 1}"):
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(urllib.request.Request(base, headers={"Host": host}), timeout=5)
            assert refused.value.code == 403
    finally:
        server.stop()
        app.state.store.close()


def test_a_manifest_naming_files_outside_its_run_is_refused(tmp_path, monkeypatch):
    from workbench.common import read_json, write_json
    from workbench.demo import create_demo
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    app = create_app(tmp_path / "projects")
    store = app.state.store
    try:
        pid = create_demo(store)["id"]
        run = store.start_run(pid, synchronous=True)
        outputs = store.run_dir(pid, run["id"]) / "outputs"
        secret = tmp_path / "projects" / "secret.txt"
        secret.write_text("private", encoding="utf-8")
        from workbench.common import file_hash
        manifest = read_json(outputs / "manifest.json")
        manifest["output_hashes"]["../../../secret.txt"] = file_hash(secret)
        write_json(outputs / "manifest.json", manifest)
        client = TestClient(app)
        base = f"/api/projects/{pid}/runs/{run['id']}"
        assert client.get(base + "/files/..%2F..%2F..%2Fsecret.txt").status_code in (400, 404)
        assert client.get(base + "/package").status_code == 400
        assert client.get(base + "/files/orders.csv").status_code == 400, "the whole manifest is refused"
    finally:
        store.close()
