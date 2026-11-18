"""AI-proposed cleaning recipes, applied only through the deterministic engine's own rules.

The model sees each source's headings and at most SAMPLE_ROWS sample rows - never
the full file - and proposes field types, missing markers, date formats, keys,
duplicate policies and relationships. Every proposal is checked against the
recipe validator before it is used; anything invalid is dropped and reported.
Relationships are never approved automatically. A wrong guess therefore sends
records to review instead of changing a published value.

This module is optional. It needs the `anthropic` package and an API credential,
and data leaves the machine only when `suggest()` is called.
"""
from __future__ import annotations

import copy
import json
import re

from .common import WorkbenchError
from .recipe import DATE_FORMATS, TYPES, starter_recipe, validate

MODEL = "claude-opus-5-5"
SAMPLE_ROWS = 20
MAX_CELL = 80

SYSTEM = """You configure a data-preparation tool for a careful analyst. For each source file you see its \
column headings and a few sample rows. Propose how each column should be typed and cleaned, which \
columns identify a record, and how tables relate.

The tool's rules, which your proposal must respect:
- Types: "text", "integer", "decimal" (dot-decimal, at most 12 fractional digits, no scientific \
notation, no currency symbols), "date" (date only, no time of day).
- A value that fails its type is held for human review, never coerced. So prefer "text" when unsure, \
and keep identifiers, codes, ZIP/postal codes and phone numbers as text even if they look numeric.
- Columns holding a time of day must stay text. Use "date" only when values are plain dates or always \
midnight.
- Floats with long noisy tails (such as 12.658579999999999) cannot be exact decimals: use text.
- "missing" lists literal markers meaning no value (for example "NA", "N/A", "null", "-"). Empty cells \
are already missing. Only list markers you see or that the file's conventions clearly imply; never list \
a marker that is also a legitimate value in that column (for example "NA" in a continent column).
- date_format must be one of the allowed formats, or "" when the column is not a date.
- key: the column(s) that uniquely identify a record in that file, or [] if no reliable key exists.
- duplicate_policy: "flag" (hold exact repeated rows for review), "keep_all" (repeats are legitimate \
separate events), or "drop_exact" (exact repeats are export noise).
- joins: relate a base table to a lookup table by matching columns. If the lookup has several rows per \
key (for example order lines per order), give aggregations (count/sum/min/max) so the base never \
multiplies; otherwise give no aggregations.

Base every choice on the sample and the headings. Give a short reason for each non-text type, each \
missing marker, each key and each join."""

SCHEMA = {
    "type": "object",
    "properties": {
        "tables": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "source_file": {"type": "string"},
                "table_name": {"type": "string"},
                "key": {"type": "array", "items": {"type": "string"}},
                "key_reason": {"type": "string"},
                "duplicate_policy": {"type": "string", "enum": ["flag", "keep_all", "drop_exact"]},
                "columns": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "type": {"type": "string", "enum": sorted(TYPES)},
                        "missing": {"type": "array", "items": {"type": "string"}},
                        "date_format": {"type": "string", "enum": [""] + sorted(DATE_FORMATS)},
                        "reason": {"type": "string"},
                    },
                    "required": ["heading", "type", "missing", "date_format", "reason"],
                    "additionalProperties": False,
                }},
            },
            "required": ["source_file", "table_name", "key", "key_reason", "duplicate_policy", "columns"],
            "additionalProperties": False,
        }},
        "joins": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "base_file": {"type": "string"},
                "lookup_file": {"type": "string"},
                "base_columns": {"type": "array", "items": {"type": "string"}},
                "lookup_columns": {"type": "array", "items": {"type": "string"}},
                "aggregations": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["count", "sum", "min", "max"]},
                        "column": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["op", "column", "name"],
                    "additionalProperties": False,
                }},
                "reason": {"type": "string"},
            },
            "required": ["base_file", "lookup_file", "base_columns", "lookup_columns", "aggregations", "reason"],
            "additionalProperties": False,
        }},
    },
    "required": ["tables", "joins"],
    "additionalProperties": False,
}


def describe(sources) -> str:
    """The only data the model sees: headings and a bounded sample of each source."""
    parts = []
    for s in sources:
        rows = [[(v if len(v) <= MAX_CELL else v[:MAX_CELL] + "…") for v in map(str, r)] for r in s["sample"][:SAMPLE_ROWS]]
        parts.append(json.dumps({"source_file": s["filename"], "headings": s["headers"], "sample_rows": rows}, ensure_ascii=False))
    return "\n".join(parts)


def ask_model(sources, client=None, model=MODEL) -> dict:
    """Call Claude for a proposal. Returns the parsed JSON object."""
    if client is None:
        try:
            import anthropic
        except ImportError as exc:
            raise WorkbenchError("AI suggestions need the optional `anthropic` package: pip install anthropic") from exc
        client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": "Propose a recipe for these sources (one JSON object per line):\n" + describe(sources)}],
    )
    if response.stop_reason == "refusal":
        raise WorkbenchError("The model declined to suggest a recipe for these sources.")
    if response.stop_reason == "max_tokens":
        raise WorkbenchError("The suggestion was cut off; try fewer sources at once.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _identifier(value):
    name = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return name if re.fullmatch(r"[a-z][a-z0-9_]{0,59}", name or "") else None


def apply_proposal(sources, proposal) -> dict:
    """Merge a proposal into the starter recipe, keeping only changes the validator accepts.

    Returns {"recipe", "applied", "rejected"}; applied and rejected are lists of
    {"where", "change", "reason"} records for the user to review.
    """
    recipe = starter_recipe(sources)
    applied, rejected = [], []
    by_file = {s["filename"]: s for s in sources}
    table_of = {a["id"]: t for t in recipe["tables"] for a in t["sources"]}

    def field_for(table, source, heading):
        mapping = next(a["mapping"] for a in table["sources"] if a["id"] == source["id"])
        name = next((f for f, h in mapping.items() if h == heading), None)
        return next((f for f in table["fields"] if f["name"] == name), None)

    def attempt(where, change, reason, mutate):
        trial = copy.deepcopy(recipe)
        try:
            mutate(trial)
            validate(trial, sources)
        except (WorkbenchError, KeyError, StopIteration, TypeError, ValueError) as exc:
            rejected.append({"where": where, "change": change, "reason": f"{reason} — rejected: {exc}"})
            return False
        recipe.clear()
        recipe.update(trial)
        applied.append({"where": where, "change": change, "reason": reason})
        return True

    for proposed in proposal.get("tables", []):
        source = by_file.get(proposed.get("source_file"))
        if not source:
            rejected.append({"where": proposed.get("source_file"), "change": "table", "reason": "unknown source file"})
            continue
        tname = table_of[source["id"]]["name"]
        for col in proposed.get("columns", []):
            heading = col.get("heading")
            where = f"{tname}.{heading}"

            def mutate(r, col=col, heading=heading, tname=tname):
                t = next(t for t in r["tables"] if t["name"] == tname)
                f = field_for(t, source, heading)
                if f is None:
                    raise KeyError(f"no column {heading!r}")
                f["type"] = col["type"]
                if col["type"] == "date":
                    f["date_format"] = col["date_format"] or "%Y-%m-%d"
                if col.get("missing"):
                    f["missing"] = sorted(set(f.get("missing", [])) | set(col["missing"]))

            current = field_for(table_of[source["id"]], source, heading) if heading in source["headers"] else None
            if current is None:
                rejected.append({"where": where, "change": "column", "reason": "unknown heading"})
                continue
            if current["type"] == col["type"] and not col.get("missing") and col["type"] != "date":
                continue  # nothing to change
            change = col["type"] + (f" ({col['date_format']})" if col["type"] == "date" else "")
            if col.get("missing"):
                change += f", missing {col['missing']}"
            attempt(where, change, col.get("reason", ""), mutate)

        def set_table(r, proposed=proposed, tname=tname, source=source):
            t = next(t for t in r["tables"] if t["name"] == tname)
            t["key"] = [field_for(t, source, h)["name"] for h in proposed.get("key", [])]
            t["duplicate_policy"] = proposed["duplicate_policy"]
        attempt(tname, f"key {proposed.get('key', [])}, {proposed.get('duplicate_policy')}", proposed.get("key_reason", ""), set_table)

        new_name = _identifier(proposed.get("table_name"))
        if new_name and new_name != tname and len(table_of[source["id"]]["sources"]) == 1:
            def rename(r, tname=tname, new_name=new_name):
                next(t for t in r["tables"] if t["name"] == tname)["name"] = new_name
            if attempt(tname, f"rename to {new_name}", "clearer table name", rename):
                tname = new_name
        table_of = {a["id"]: t for t in recipe["tables"] for a in t["sources"]}

    for join in proposal.get("joins", []):
        base_src, look_src = by_file.get(join.get("base_file")), by_file.get(join.get("lookup_file"))
        where = f"{join.get('base_file')} → {join.get('lookup_file')}"
        if not base_src or not look_src or base_src is look_src:
            rejected.append({"where": where, "change": "join", "reason": "unknown or identical source files"})
            continue
        base_t, look_t = table_of[base_src["id"]], table_of[look_src["id"]]

        def add_join(r, join=join, base_t=base_t, look_t=look_t, base_src=base_src, look_src=look_src):
            bt = next(t for t in r["tables"] if t["name"] == base_t["name"])
            lt = next(t for t in r["tables"] if t["name"] == look_t["name"])
            entry = {"table": lt["name"], "how": "left", "approved": False,
                     "left_on": [field_for(bt, base_src, h)["name"] for h in join["base_columns"]],
                     "right_on": [field_for(lt, look_src, h)["name"] for h in join["lookup_columns"]]}
            if join.get("aggregations"):
                entry["aggregations"] = [{"op": a["op"], "field": field_for(lt, look_src, a["column"])["name"],
                                          "name": _identifier(a["name"]) or f"{a['op']}_{a['column']}"}
                                         for a in join["aggregations"]]
            view = next((v for v in r.setdefault("views", []) if v["base"] == bt["name"]), None)
            if view is None:
                view = {"name": f"{bt['name']}_combined", "base": bt["name"], "joins": []}
                r["views"].append(view)
            view["joins"].append(entry)
        attempt(where, "join" + (" with aggregation" if join.get("aggregations") else ""), join.get("reason", ""), add_join)
    return {"recipe": recipe, "applied": applied, "rejected": rejected}


def suggest(sources, client=None, model=MODEL) -> dict:
    """Ask the model for a proposal and merge whatever the validator accepts."""
    return apply_proposal(sources, ask_model(sources, client=client, model=model))
