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


def test_leftovers_from_an_earlier_session_are_removed_on_start(store):
    (store.runtime / "interrupted-run").mkdir()
    (store.runtime / "interrupted-run" / "data.duckdb").write_bytes(b"partial")
    (store.runtime / "abandoned-download.zip").write_bytes(b"PK")
    store.worker.shutdown(wait=True)
    restarted = ProjectStore(store.root)
    try:
        assert list(restarted.runtime.iterdir()) == []
    finally:
        restarted.worker.shutdown(wait=True)
