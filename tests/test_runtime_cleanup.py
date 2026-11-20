import copy

from workbench.common import read_json
from workbench.demo import create_demo
from workbench.store import ProjectStore
from conftest import completed


def test_a_completed_run_keeps_its_package_and_drops_its_working_files(store):
    project = create_demo(store)
    run = completed(store, project["id"])
    assert (store.run_dir(project["id"], run["id"]) / "outputs" / "data.duckdb").is_file()
    assert list(store.runtime.iterdir()) == []


def test_a_failed_run_drops_its_working_files(store):
    project = copy.deepcopy(create_demo(store))
    rid = "0" * 16 + "-deadbeef"
    (store.runtime / rid).mkdir()
    (store.runtime / rid / "data.duckdb").write_bytes(b"partial")
    project["recipe"] = None  # processing cannot start without a recipe
    store._execute(project, rid, "fingerprint")
    assert read_json(store.run_dir(project["id"], rid) / "status.json")["state"] == "failed"
    assert list(store.runtime.iterdir()) == []


def test_leftovers_from_ended_sessions_are_removed_on_start(store):
    base = store.runtime.parent
    (base / "0123456789abcdef-deadbeef").mkdir()  # a run folder in the layout before 2.2.2
    (base / "0123456789abcdef-deadbeef" / "data.duckdb").write_bytes(b"partial")
    (base / "abandoned-download.zip").write_bytes(b"PK")
    (base / "session-ended").mkdir()  # a session that ended without cleaning up: its lock is free
    (base / "session-ended" / "data.duckdb").write_bytes(b"partial")
    (base / "session-ended.lock").write_bytes(b"")
    restarted = ProjectStore(store.root)
    try:
        assert sorted(p.name for p in base.iterdir()) == sorted(
            [store.runtime.name, store.runtime.name + ".lock", restarted.runtime.name, restarted.runtime.name + ".lock"])
    finally:
        restarted.worker.shutdown(wait=True)


def test_a_second_app_on_the_same_projects_keeps_the_first_apps_working_files(store):
    working = store.runtime / "0123456789abcdef-deadbeef" / "data.duckdb"
    working.parent.mkdir()
    working.write_bytes(b"in use")
    (store.runtime / "export.zip").write_bytes(b"PK")
    second = ProjectStore(store.root)
    try:
        assert working.read_bytes() == b"in use"
        assert (store.runtime / "export.zip").is_file()
        assert second.runtime != store.runtime
    finally:
        second.worker.shutdown(wait=True)
