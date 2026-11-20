"""Install the setup program as a user would, start the installed app, then uninstall it.

    python tools/installer_check.py --setup dist/Data-Workbench-3.0.0-setup.exe --evidence installer-check.json

Checks that setup needs no administrator rights, installs the bundled Python and the installed
marker, creates the Start-menu shortcut, and that the installed app answers with its projects in
%LOCALAPPDATA%/DataWorkbench/projects. Then it installs the same setup again over the top (an
upgrade), uninstalls, and checks the program folder is gone while the projects folder remains.
Meant for a disposable CI machine: it installs into the current user's profile.
"""
import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    local = Path(os.environ["LOCALAPPDATA"])
    app = local / "Programs" / "Data Workbench"
    projects = local / "DataWorkbench" / "projects"
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Data Workbench.lnk"
    quiet = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]

    subprocess.run([str(args.setup.resolve()), *quiet, "/LOG=" + str(args.evidence.with_suffix(".install.log"))], check=True, timeout=600)
    python = app / "runtime" / "python" / "python.exe"
    assert (app / "runtime" / "python" / "pythonw.exe").is_file(), "pythonw.exe missing"
    assert (app / "installed.json").is_file(), "installed marker missing"
    assert not (app / "Start Workbench.cmd").exists(), "the ZIP launcher should not be installed"
    assert shortcut.is_file(), f"Start-menu shortcut missing: {shortcut}"

    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "DATA_WORKBENCH_PROJECTS")}
    system = os.environ.get("SystemRoot", r"C:\Windows")
    env["PATH"] = os.pathsep.join([system + r"\System32", system])  # no other Python is reachable
    check = subprocess.run([str(python), "-B", "-m", "workbench.desktop", "--check"], cwd=app, env=env,
                           capture_output=True, text=True, timeout=120)
    assert check.returncode == 0, check.stdout + check.stderr
    found = re.search(r"with projects in (.+)$", check.stdout.strip())
    assert found and Path(found.group(1)).resolve() == projects.resolve(), check.stdout
    engine = subprocess.run([str(python), "-B", "-c", "import webview; from webview.platforms import winforms"],
                            cwd=app, env=env, capture_output=True, text=True, timeout=120)
    assert engine.returncode == 0, engine.stderr
    (projects / "keep-me.txt").write_text("a user's file", encoding="utf-8")

    subprocess.run([str(args.setup.resolve()), *quiet], check=True, timeout=600)  # upgrade in place
    assert (app / "installed.json").is_file() and (projects / "keep-me.txt").is_file()

    uninstaller = next(app.glob("unins*.exe"))
    subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=600)
    for _ in range(60):  # the uninstaller finishes removing itself in a moment
        if not app.exists():
            break
        time.sleep(1)
    assert not app.exists(), f"program folder left behind: {list(app.rglob('*'))[:10]}"
    assert not shortcut.exists(), "Start-menu shortcut left behind"
    assert (projects / "keep-me.txt").read_text(encoding="utf-8") == "a user's file", "projects were removed"

    record = {"passed": True, "setup": args.setup.name, "installed_to": "%LOCALAPPDATA%/Programs/Data Workbench",
              "projects": "%LOCALAPPDATA%/DataWorkbench/projects", "start_menu_shortcut": True, "window_engine_loads": True,
              "upgrade_in_place_keeps_projects": True, "uninstall_removes_program_keeps_projects": True}
    args.evidence.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
