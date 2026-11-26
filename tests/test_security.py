import json
import os
import subprocess
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from workbench import desktop
from workbench.app import allowed_host, create_app
from workbench.common import WorkbenchError, contained, read_json, write_json
from workbench.demo import create_demo
from workbench.store import ProjectStore
from conftest import completed


@pytest.mark.parametrize("host,port,server,ok", [
    ("127.0.0.1:8810", 8810, "127.0.0.1", True),
    ("localhost:8810", 8810, "127.0.0.1", True),
    ("127.0.0.1:8811", 8810, "127.0.0.1", False),       # another local server's address
    ("attacker.example:8810", 8810, "127.0.0.1", False),  # DNS rebinding keeps the attacker's name
    ("127.0.0.1.attacker.example:8810", 8810, "127.0.0.1", False),
    ("127.0.0.1", 8810, "127.0.0.1", False),
    ("", 8810, "127.0.0.1", False),
    ("testserver", 80, "testserver", True),
    ("testserver", 8810, "127.0.0.1", False),  # a machine really named testserver
])
def test_only_this_exact_loopback_address_is_served(host, port, server, ok):
    assert allowed_host(host, port, server) is ok


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
        for host in (f"attacker.example:{server.port}", f"127.0.0.1:{server.port + 1}", "testserver"):
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


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    instance = create_app(tmp_path / "projects")
    yield instance
    instance.state.store.close()


def test_no_state_changing_endpoint_accepts_a_cross_site_request(app):
    """What another page can send without asking first: a form post or a no-cors fetch."""
    client = TestClient(app)
    routes = [(method, route.path) for route in app.routes
              for method in (getattr(route, "methods", None) or ()) if method not in ("GET", "HEAD")]
    assert len(routes) >= 10
    attempts = [
        {"data": {"name": "x"}},                                              # urlencoded form
        {"files": {"file": ("a.csv", b"a,b\n1,2\n")}},                        # multipart form
        {"content": b'{"name": "x"}', "headers": {"content-type": "text/plain"}},  # no-cors fetch
    ]
    origins = [{"sec-fetch-site": "cross-site", "origin": "http://attacker.example"},
               {"sec-fetch-site": "same-site", "origin": "http://127.0.0.1:9999"},
               {"origin": "http://attacker.example"},  # a browser without fetch metadata
               {}]
    for method, path in routes:
        url = path.replace("{", "").replace("}", "")
        for attempt in attempts:
            for extra in origins:
                headers = {**attempt.get("headers", {}), **extra}
                reply = client.request(method, url, **{**attempt, "headers": headers})
                assert reply.status_code == 403, (method, path, extra)
        # Even holding the right token, another origin is refused.
        for extra in ({"origin": "http://127.0.0.1:9999"}, {"sec-fetch-site": "cross-site"}):
            reply = client.request(method, url, headers={"x-workbench-token": app.state.token, **extra})
            assert reply.status_code == 403, (method, path, extra)
    assert app.state.store.list() == []


def test_a_preflight_grants_another_origin_nothing(app):
    reply = TestClient(app).options("/api/projects", headers={
        "origin": "http://attacker.example", "access-control-request-method": "POST",
        "access-control-request-headers": "x-workbench-token"})
    assert not any(name.lower().startswith("access-control-") for name in reply.headers)


@pytest.mark.parametrize("name", [
    "", ".", "..", "../secret.txt", "..\\secret.txt", "sub/file.csv", "/etc/passwd", "C:\\Windows\\win.ini",
    "C:secret.txt", "\\\\server\\share\\x", "//server/share/x", "orders.csv:stream", "a\0b", 7,
])
def test_only_plain_names_inside_the_folder_are_contained(tmp_path, name):
    with pytest.raises(WorkbenchError):
        contained(tmp_path, [name])


@pytest.mark.parametrize("folder", [
    "\\\\server\\share", "//server/share", '"\\\\server\\share"', "\\\\?\\C:\\projects", "\\\\.\\pipe\\x",
    "..", "projects", "C:projects",
])
def test_copying_projects_refuses_network_device_and_relative_paths(store, folder):
    with pytest.raises(WorkbenchError, match="not accepted|full path"):
        store.import_projects(folder)


@pytest.mark.parametrize("field,value", [
    ("id", "../../../outside"), ("id", "C:\\outside"), ("stored_name", "../../../../secret.csv"),
    ("stored_name", "\\\\server\\share\\x.csv"),
])
def test_a_project_file_pointing_outside_is_neither_opened_nor_copied(tmp_path, store, field, value):
    old = ProjectStore(tmp_path / "old")
    pid = create_demo(old)["id"]
    record = read_json(old.root / pid / "project.json")
    record["sources"][0][field] = value
    write_json(old.root / pid / "project.json", record)
    with pytest.raises(WorkbenchError, match="damaged"):
        old.get(pid)
    old.close()
    with pytest.raises(WorkbenchError, match="damaged"):
        store.import_projects(str(tmp_path / "old"))
    assert store.list() == []


def test_a_project_file_cannot_claim_another_projects_identity(store):
    first, second = store.create("one"), store.create("two")
    write_json(store.root / first["id"] / "project.json", second)
    with pytest.raises(WorkbenchError, match="damaged"):
        store.get(first["id"])
    assert [p["name"] for p in store.list()] == ["two"], "the impostor record is not listed"


def make_folder_link(link, target):
    if os.name == "nt":  # a junction needs no special rights, unlike a symbolic link
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_a_linked_folder_inside_a_project_is_not_followed(tmp_path, store):
    old = ProjectStore(tmp_path / "old")
    pid = old.create("linked")["id"]
    old.close()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private", encoding="utf-8")
    make_folder_link(old.root / pid / "raw", outside)
    with pytest.raises(WorkbenchError, match="links"):
        store.import_projects(str(tmp_path / "old"))
    assert not (store.root / pid).exists()
    assert not list(store.root.rglob("private.txt"))


def test_a_link_inside_outputs_is_not_contained(tmp_path):
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outside").mkdir()
    make_folder_link(tmp_path / "outputs" / "shortcut", tmp_path / "outside")
    with pytest.raises(WorkbenchError):
        contained(tmp_path / "outputs", ["shortcut"])


def test_an_unsafe_earlier_package_is_rebuilt_without_opening_what_it_names(store, monkeypatch):
    import workbench.store as module
    pid = create_demo(store)["id"]
    run = completed(store, pid)
    manifest_path = store.run_dir(pid, run["id"]) / "outputs" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["output_hashes"] = {"../../../project.json": "0"}
    write_json(manifest_path, manifest)
    real, hashed = module.file_hash, []
    monkeypatch.setattr(module, "file_hash", lambda path: hashed.append(str(path)) or real(path))
    again = completed(store, pid)
    assert again["id"] != run["id"]
    assert not any(".." in path for path in hashed)


@pytest.mark.parametrize("part", ["raw", "runs"])
def test_a_project_folder_linking_elsewhere_is_not_used(tmp_path, store, part):
    pid = store.create("linked")["id"]
    outside = tmp_path / "outside"
    outside.mkdir()
    make_folder_link(store.root / pid / part, outside)
    with pytest.raises(WorkbenchError, match="link to another location"):
        store.get(pid) if part == "raw" else store.run_dir(pid, "abc123")


def test_a_linked_projects_folder_inside_the_chosen_one_is_not_followed(tmp_path, store):
    old = ProjectStore(tmp_path / "elsewhere")
    old.create("not chosen")
    old.close()
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    make_folder_link(chosen / "projects", tmp_path / "elsewhere")
    with pytest.raises(WorkbenchError, match="No Data Workbench projects"):
        store.import_projects(str(chosen))
