from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2.0.1"
ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 50
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 250 * 1024 * 1024
MAX_ROWS = 1_000_000
MAX_COLUMNS = 200
PREVIEW_ROWS = 100
WORKBOOK_ROWS = 1000


class WorkbenchError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def identifier(value):
    value = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    if not value or not value[0].isalpha():
        value = "field_" + value
    return value[:60]


def q(value):
    return '"' + str(value).replace('"', '""') + '"'


def lit(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def runtime_root():
    base = Path(os.environ["DATA_WORKBENCH_RUNTIME"]) if "DATA_WORKBENCH_RUNTIME" in os.environ else Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "DataWorkbench" / "runtime"
    base.mkdir(parents=True, exist_ok=True)
    return base


def safe_text(value):
    """Make spreadsheet formula-looking strings inert, without changing numbers.

    Used for the review workbook, which is opened in a spreadsheet by design.
    """
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


# Plain data that merely starts with a sign: numbers, coordinates, phone numbers,
# ranges. Without letters or formula operators like | ! = it cannot call a function.
PLAIN_SIGNED = re.compile(r"[+-][0-9 .,()/:+-]*")


def csv_safe_text(value):
    """Escape CSV text only when a spreadsheet could run it as a formula.

    CSV exports are data, so values such as "-67.5, -68.1" or "+44 20 7946 0000"
    stay exactly as recorded. Anything that could start a formula or DDE call
    (=..., @..., +cmd|..., -2+3+cmd|...) is still prefixed with an apostrophe.
    """
    if not isinstance(value, str):
        return value
    text = value.lstrip()
    if text.startswith(("=", "@", "\t", "\r")):
        return "'" + value
    if text.startswith(("+", "-")) and not PLAIN_SIGNED.fullmatch(text):
        return "'" + value
    return value
