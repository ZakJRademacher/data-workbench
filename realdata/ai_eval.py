"""Score AI-suggested recipes against hand-built reference recipes on real data.

    python -m realdata.ai_eval --cache .realdata-cache --out ai-eval.json

For each dataset the same sources get three recipes: the built-in rule-based
starter (baseline), the AI proposal, and the reference recipe used by the
real-data regression suite. Each is scored on column types, keys and joins
against the reference, then run through the workbench to count records held
for review, joins the engine blocks, and totals that differ from the
reference run. Calls the Claude API once per dataset (billed to your key).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cases(cache: Path, work: Path):
    from realdata import inputs
    from realdata import test_realdata as t
    olist = inputs.olist(cache, work, full=False)
    flights = inputs.flights(cache, work, full=False)
    return [
        ("olist", [olist[k] for k in ("orders", "items", "payments", "customers", "products")], t.configure_olist),
        ("nycflights13", [flights[k] for k in ("flights", "planes", "airports", "airlines", "weather")], t.configure_flights),
        ("online_retail", [inputs.retail(cache, work, full=False)], t.configure_retail),
        ("airport_codes", [cache / "airports/airport-codes.csv"], lambda r: r["tables"][0].update(key=["ident"])),
    ]


def describe_recipe(recipe, sources):
    """Express a recipe in source terms (file, heading) so differently named tables compare."""
    by_id = {s["id"]: s for s in sources}
    columns, keys, joins = {}, {}, set()
    owner = {}
    for t in recipe["tables"]:
        fields = {f["name"]: f for f in t["fields"]}
        for a in t["sources"]:
            name = by_id[a["id"]]["filename"]
            owner[t["name"]] = (name, a["mapping"])
            for fname, heading in a["mapping"].items():
                if heading:
                    columns[(name, heading)] = fields[fname]["type"]
            keys[name] = tuple(sorted(a["mapping"][k] for k in t.get("key", [])))
    for v in recipe.get("views", []):
        base_file, base_map = owner[v["base"]]
        for j in v["joins"]:
            look_file, look_map = owner[j["table"]]
            joins.add((base_file, look_file, tuple(base_map[c] for c in j["left_on"]),
                       tuple(look_map[c] for c in j["right_on"]), bool(j.get("aggregations"))))
    return columns, keys, joins


def score(recipe, reference, sources):
    cols, keys, joins = describe_recipe(recipe, sources)
    rcols, rkeys, rjoins = describe_recipe(reference, sources)
    return {
        "type_accuracy": sum(cols[c] == rcols[c] for c in rcols) / len(rcols),
        "type_errors": sorted(f"{f}:{h} {cols[(f, h)]}≠{rcols[(f, h)]}" for f, h in rcols if cols[(f, h)] != rcols[(f, h)]),
        "keys_correct": f"{sum(keys[f] == rkeys[f] for f in rkeys)}/{len(rkeys)}",
        "joins_found": f"{len(joins & rjoins)}/{len(rjoins)}",
        "extra_joins": len(joins - rjoins),
    }


def run(store, pid, recipe):
    recipe = copy.deepcopy(recipe)
    for v in recipe.get("views", []):
        for j in v["joins"]:
            j["approved"] = True  # stands in for the analyst's approval after previewing
    try:
        store.set_recipe(pid, recipe)
        status = store.start_run(pid, synchronous=True)
        status = store.run_status(pid, status["id"])
    except Exception as exc:  # an invalid recipe is a result, not a crash
        return {"state": "rejected", "message": str(exc)}
    if status["state"] != "completed":
        return {"state": status["state"], "message": status.get("message", "")}
    m = status["manifest"]
    totals = {f"{t['name']}.{k}": v["sum"] for t in m["tables"] for k, v in t["totals"].items()}
    return {"state": "completed", "input": m["input_rows"], "review": m["review_rows"], "totals": totals,
            "views": {v["name"]: v["after"] for v in m["views"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, default=ROOT / ".realdata-cache")
    parser.add_argument("--out", type=Path, default=Path("ai-eval.json"))
    args = parser.parse_args()

    from realdata.fetch import fetch
    cache = fetch(args.cache)
    work = Path(tempfile.mkdtemp(prefix="ai-eval-"))
    os.environ["DATA_WORKBENCH_RUNTIME"] = str(work / "runtime")
    import sys
    sys.path.insert(0, str(ROOT))
    from workbench.ai_suggest import suggest
    from workbench.store import ProjectStore
    store = ProjectStore(work / "projects")

    report = {"model": None, "cases": {}}
    for name, files, configure in cases(cache, work / "inputs"):
        pid = store.create(f"eval {name}")["id"]
        for f in files:
            store.add_source(pid, Path(f).name, f)
        sources = store.get(pid)["sources"]
        baseline = store.set_recipe(pid)
        reference = copy.deepcopy(baseline)
        configure(reference)
        started = time.perf_counter()
        ai = suggest(sources)
        seconds = round(time.perf_counter() - started, 1)
        from workbench.ai_suggest import MODEL
        report["model"] = MODEL
        ref_run = run(store, pid, reference)
        entry = {"ai_seconds": seconds, "applied": ai["applied"], "rejected": ai["rejected"]}
        for label, recipe in (("baseline", baseline), ("ai", ai["recipe"])):
            result = run(store, pid, recipe)
            differing = None
            if result["state"] == "completed" and ref_run["state"] == "completed":
                shared = set(result["totals"]) & set(ref_run["totals"])
                differing = sorted(k for k in shared if result["totals"][k] is not None and ref_run["totals"][k] is not None
                                   and Decimal(result["totals"][k]) != Decimal(ref_run["totals"][k]))
            entry[label] = {**score(recipe, reference, sources), "run": {k: v for k, v in result.items() if k != "totals"},
                            "totals_differing_from_reference": differing}
        entry["reference_run"] = {k: v for k, v in ref_run.items() if k != "totals"}
        report["cases"][name] = entry
        b, a = entry["baseline"], entry["ai"]
        print(f"{name:14} types {b['type_accuracy']:.0%} -> {a['type_accuracy']:.0%}  keys {b['keys_correct']} -> {a['keys_correct']}  "
              f"joins {b['joins_found']} -> {a['joins_found']}  review {b['run'].get('review')} -> {a['run'].get('review')} "
              f"(reference {ref_run.get('review')})", flush=True)
    store.worker.shutdown(wait=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("Wrote", args.out)


if __name__ == "__main__":
    main()
