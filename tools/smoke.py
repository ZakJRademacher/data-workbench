import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.store import ProjectStore
from workbench.demo import create_demo

if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    os.environ["DATA_WORKBENCH_RUNTIME"] = str(root / "runtime")
    store = ProjectStore(root / "projects")
    project = create_demo(store)
    result = store.start_run(project["id"], synchronous=True)
    print(json.dumps({"project": project["id"], "run": result}, indent=2, default=str))
    if result["state"] != "completed":
        raise SystemExit(1)
