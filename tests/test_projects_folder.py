import pytest
from fastapi.testclient import TestClient

from workbench import common
from workbench.app import create_app
from workbench.common import WorkbenchError, read_json, write_json
from workbench.demo import create_demo
from workbench.store import ProjectStore
from conftest import completed


def test_a_download_keeps_projects_beside_the_app_and_an_installed_copy_in_app_data(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_WORKBENCH_PROJECTS", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(common, "ROOT", tmp_path / "app")
    (tmp_path / "app").mkdir()
    assert not common.installed()
    assert common.default_projects_root() == tmp_path / "app" / "projects"
    (tmp_path / "app" / "installed.json").write_text("{}")
    assert common.installed()
    assert common.default_projects_root() == tmp_path / "appdata" / "DataWorkbench" / "projects"
    monkeypatch.setenv("DATA_WORKBENCH_PROJECTS", str(tmp_path / "chosen"))
    assert common.default_projects_root() == tmp_path / "chosen"


@pytest.fixture
def earlier(tmp_path, monkeypatch):
    """A projects folder from an earlier download, with one completed demo run."""
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    old = ProjectStore(tmp_path / "old-download" / "projects")
    project = create_demo(old)
    run = completed(old, project["id"])
    old.close()
    return old.root, project, run


def test_projects_are_copied_identically_and_nothing_is_overwritten(store, earlier):
    folder, project, run = earlier
    first = store.import_projects(str(folder))
    assert first["copied"] == [project["name"]] and first["skipped"] == []
    assert store.get(project["id"])["latest_run"] == run["id"]
    assert store.run_status(project["id"], run["id"])["state"] == "completed"
    package = store.run_dir(project["id"], run["id"]) / "outputs" / "manifest.json"
    assert package.read_bytes() == (folder / project["id"] / "runs" / run["id"] / "outputs" / "manifest.json").read_bytes()

    renamed = store.get(project["id"])
    renamed["name"] = "Changed here"
    store.save(renamed)
    again = store.import_projects(str(folder))
    assert again["copied"] == [] and again["skipped"] == [project["name"]]
    assert store.get(project["id"])["name"] == "Changed here", "an existing project is never overwritten"
    assert not (store.root / ".importing").exists()


def test_choosing_the_download_folder_finds_its_projects(store, earlier):
    folder, project, _ = earlier
    assert store.import_projects(str(folder.parent))["copied"] == [project["name"]]


def test_a_run_left_unfinished_in_the_old_folder_arrives_interrupted(store, earlier):
    folder, project, _ = earlier
    write_json(folder / project["id"] / "runs" / "deadbeef" / "status.json",
               {"id": "deadbeef", "state": "running", "fingerprint": "", "progress": 10, "message": "in progress"})
    store.import_projects(str(folder))
    assert read_json(store.run_dir(project["id"], "deadbeef") / "status.json")["state"] == "interrupted"
    assert read_json(folder / project["id"] / "runs" / "deadbeef" / "status.json")["state"] == "running", "the original is untouched"


def test_a_copy_that_differs_from_the_original_is_discarded(store, earlier, monkeypatch):
    folder, project, _ = earlier
    import workbench.store as module
    real = module.file_hash
    monkeypatch.setattr(module, "file_hash", lambda path: "different" if ".importing" in str(path) else real(path))
    with pytest.raises(WorkbenchError, match="identical copy"):
        store.import_projects(str(folder))
    assert not (store.root / project["id"]).exists()
    assert not (store.root / ".importing").exists()
    assert store.list() == []


@pytest.mark.parametrize("folder,message", [("", "Choose"), ("C:/no/such/folder/anywhere", "does not exist")])
def test_unusable_folders_are_explained(store, folder, message):
    with pytest.raises(WorkbenchError, match=message):
        store.import_projects(folder)


def test_a_folder_without_projects_or_this_apps_own_folder_is_refused(store, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(WorkbenchError, match="No Data Workbench projects"):
        store.import_projects(str(tmp_path / "empty"))
    with pytest.raises(WorkbenchError, match="own projects folder"):
        store.import_projects(str(store.root))


def test_the_api_copies_projects_and_reports_the_storage_mode(tmp_path, monkeypatch, earlier):
    folder, project, _ = earlier
    app = create_app(tmp_path / "new" / "projects")
    try:
        client = TestClient(app)
        session = client.get("/api/session").json()
        assert session["installed"] is False and session["desktop"] is False
        headers = {"x-workbench-token": session["token"]}
        assert client.post("/api/projects/import", json={"folder": str(folder)}, headers=headers).json()["copied"] == [project["name"]]
        assert [p["id"] for p in client.get("/api/projects").json()] == [project["id"]]
        assert client.post("/api/desktop/choose-folder", json={}, headers=headers).status_code == 400, "no folder picker outside the window"
    finally:
        app.state.store.close()
