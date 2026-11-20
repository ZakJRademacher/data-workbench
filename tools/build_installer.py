"""Build the Windows setup program from the bundled download.

    python tools/build_installer.py --download dist/Data-Workbench-3.0.0-windows-x64.zip

The setup installs exactly the files of that already-checked download (see
tools/build_portable.py and tools/clean_install_check.py), plus installed.json, using
packaging/workbench.iss. Needs Inno Setup 6 (ISCC.exe on PATH or in its default folder).
Writes Data-Workbench-<version>-setup.exe and a .json record next to the download.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workbench.common import VERSION


def iscc():
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        for candidate in (Path(base or "") / "Inno Setup 6" / "ISCC.exe", Path(base or "") / "Programs" / "Inno Setup 6" / "ISCC.exe"):
            if base and candidate.is_file():
                return str(candidate)
    raise SystemExit("Inno Setup 6 is required: install it, or put ISCC.exe on PATH.")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--download", type=Path, required=True, help="the bundled Windows ZIP from build_portable.py")
    parser.add_argument("--output", type=Path, help="folder for the setup program (default: beside the download)")
    args = parser.parse_args()
    download = args.download.resolve()
    output = (args.output or download.parent).resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="workbench-setup-"))
    try:
        with zipfile.ZipFile(download) as archive:
            names = archive.namelist()
            if any(n.startswith(("/", "\\")) or ".." in Path(n).parts for n in names):
                raise SystemExit("The download contains unsafe paths.")
            archive.extractall(stage)
        app = stage / "data-workbench"
        if not (app / "runtime" / "python" / "pythonw.exe").is_file():
            raise SystemExit("Use the bundled download (with runtime/python), not the source-only ZIP.")
        subprocess.run([iscc(), "/Q", f"/DAppVersion={VERSION}", f"/DStage={app}", f"/DOutputDir={output}",
                        str(ROOT / "packaging" / "workbench.iss")], check=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    setup = output / f"Data-Workbench-{VERSION}-setup.exe"
    record = {"filename": setup.name, "bytes": setup.stat().st_size, "sha256": sha256(setup),
              "built_from": {"filename": download.name, "sha256": sha256(download)}, "signed": False}
    setup.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
