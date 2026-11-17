"""Download the pinned public datasets and verify every byte against its recorded hash.

    python -m realdata.fetch --cache .realdata-cache

Each URL names a fixed commit or release, and each file must match its SHA-256,
so a changed upstream file fails loudly instead of silently changing the tests.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASETS = json.loads((HERE / "datasets.json").read_text(encoding="utf-8"))
FLIGHT_TABLES = ("flights", "planes", "airports", "airlines", "weather")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, attempts: int = 4) -> None:
    partial = target.with_suffix(target.suffix + ".part")
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as out:
                while block := response.read(1 << 20):
                    out.write(block)
            partial.replace(target)
            return
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(2 ** attempt)


def extract_flights(archive: Path, folder: Path) -> None:
    """Read the five CSVs out of the nycflights13 sdist without extracting anything else."""
    with tarfile.open(archive, "r:gz") as tar:
        members = {Path(m.name).name: m for m in tar.getmembers() if m.isfile()}
        for name in FLIGHT_TABLES:
            if name == "flights":
                packed = tar.extractfile(members["flights.csv.zip"]).read()
                data = zipfile.ZipFile(io.BytesIO(packed)).read("flights.csv")
            else:
                data = tar.extractfile(members[f"{name}.csv"]).read()
            (folder / f"{name}.csv").write_bytes(data)


def fetch(cache: Path) -> Path:
    cache = Path(cache).resolve()
    for name, item in DATASETS.items():
        target = cache / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or sha256(target) != item["sha256"]:
            print(f"Downloading {name} ...", flush=True)
            download(item["url"], target)
        actual = sha256(target)
        if actual != item["sha256"]:
            target.unlink()
            raise SystemExit(f"{name}: SHA-256 {actual} does not match the pinned {item['sha256']}. "
                             "The upstream file changed; review it before updating datasets.json.")
    flights = cache / "flights"
    if not all((flights / f"{n}.csv").exists() for n in FLIGHT_TABLES):
        extract_flights(cache / DATASETS["nycflights13"]["path"], flights)
    return cache


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=Path(".realdata-cache"))
    print("Verified datasets in", fetch(parser.parse_args().cache))
