import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.store import ProjectStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_WORKBENCH_RUNTIME", str(tmp_path / "runtime"))
    instance = ProjectStore(tmp_path / "projects")
    yield instance
    instance.worker.shutdown(wait=True)


def add_csv(store, pid, tmp_path, name, content, encoding="utf-8"):
    path = tmp_path / name
    path.write_text(content, encoding=encoding, newline="")
    return store.add_source(pid, name, path)


def completed(store, pid):
    result = store.start_run(pid, synchronous=True)
    assert result["state"] == "completed", result
    return result
