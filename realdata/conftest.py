import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODE = os.environ.get("REALDATA_MODE", "sample")
if MODE not in ("sample", "full"):
    raise pytest.UsageError("REALDATA_MODE must be sample or full")
FULL = MODE == "full"
REPORT = {"mode": MODE, "cases": {}}


@pytest.fixture(scope="session")
def cache():
    from realdata.fetch import fetch
    return fetch(Path(os.environ.get("REALDATA_CACHE", ROOT / ".realdata-cache")))


@pytest.fixture(scope="session")
def work(tmp_path_factory):
    return tmp_path_factory.mktemp("inputs")


@pytest.fixture(scope="session")
def store(tmp_path_factory):
    root = tmp_path_factory.mktemp("workbench")
    os.environ["DATA_WORKBENCH_RUNTIME"] = str(root / "runtime")
    from workbench.store import ProjectStore
    instance = ProjectStore(root / "projects")
    yield instance
    instance.worker.shutdown(wait=True)


class Runner:
    """Create a project from files, let a callback configure the suggested recipe, and run it."""

    def __init__(self, store):
        self.store = store

    def __call__(self, case, files, configure=lambda recipe: None):
        pid = self.store.create(case)["id"]
        profiles = {Path(f).name: self.store.add_source(pid, Path(f).name, f) for f in files}
        recipe = self.store.set_recipe(pid)
        configure(recipe)
        self.store.set_recipe(pid, recipe)
        started = time.perf_counter()
        status = self.store.start_run(pid, synchronous=True)
        status = self.store.run_status(pid, status["id"])
        assert status["state"] == "completed", status
        manifest = status["manifest"]
        REPORT["cases"][case] = {
            "seconds": round(time.perf_counter() - started, 1),
            "input_rows": manifest["input_rows"],
            "tables": {t["name"]: {k: t[k] for k in ("input", "accepted", "review", "excluded")} for t in manifest["tables"]},
        }
        return pid, profiles, manifest, self.store.run_dir(pid, status["id"]) / "outputs"


@pytest.fixture(scope="session")
def run(store):
    return Runner(store)


def pytest_sessionfinish(session):
    target = os.environ.get("REALDATA_REPORT")
    if target and REPORT["cases"]:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
