from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

import duckdb

from .common import MAX_ROWS, VERSION, WorkbenchError, dumps, file_hash, lit, now, q, safe_text, write_json
from .ingest import META, stage_source
from .recipe import validate

WHITESPACE = ''.join(chr(c) for c in list(range(9,14)) + list(range(28,33)) + [133,160,5760] + list(range(8192,8203)) + [8232,8233,8239,8287,12288])


def field_expr(field, original):
    raw = f"trim({original}, {lit(WHITESPACE)})" if field.get("trim", True) else original
    raw = f"CASE WHEN {raw} IN ({','.join(lit(x) for x in [''] + field.get('missing', []))}) THEN NULL ELSE {raw} END"
    if field.get("mapping"):
        fold = field.get('case_insensitive', False)
        test = f'lower({raw})' if fold else raw
        raw = f"CASE {test} " + " ".join(f"WHEN {lit(k.lower() if fold else k)} THEN {lit(v)}" for k, v in field["mapping"].items()) + f" ELSE {raw} END"
    if field.get('case_insensitive') and field.get('allowed'):
        raw = f"CASE lower({raw}) " + ' '.join(f'WHEN {lit(v.lower())} THEN {lit(v)}' for v in field['allowed']) + f' ELSE {raw} END'
    kind = field["type"]
    if kind == "text":
        return raw, "FALSE"
    if kind == "date":
        value = f"try_strptime({raw}, {lit(field.get('date_format', '%Y-%m-%d'))})::DATE"
        width_check = f" OR strftime({value}, {lit(field.get('date_format', '%Y-%m-%d'))}) != {raw}" if field.get('date_exact', True) else ''
        return value, f"({raw} IS NOT NULL AND ({value} IS NULL{width_check}))"
    value = raw
    for symbol in field.get("strip_symbols", ""):
        value = f"replace({value}, {lit(symbol)}, '')"
    thousands = field.get("thousands_separator", "")
    decimal = field.get("decimal_separator", ".")
    # Validate grouping before removing separators, so 1,2,3 cannot become 123.
    grouping_bad = "FALSE"
    if thousands:
        pattern = "[+-]?(?:[0-9]+|[0-9]{1,3}(?:" + re.escape(thousands) + "[0-9]{3})+)(?:" + re.escape(decimal) + "[0-9]+)?"
        grouping_bad = f"({value} IS NOT NULL AND NOT regexp_full_match({value}, {lit(pattern)}))"
        value = f"replace({value}, {lit(thousands)}, '')"
    if decimal != ".":
        value = f"replace({value}, {lit(decimal)}, '.')"
    sign = '' if field.get('unsigned') else '[+-]?'
    valid = f"regexp_full_match({value}, {lit(sign + '[0-9]+(?:\\.[0-9]+)?')})"
    fraction = f"split_part({value}, '.', 2)"
    if field.get('precision_mode', 'nonzero') == 'nonzero':
        fraction = f"rtrim({fraction}, '0')"
    precision = f"length({fraction}) <= {field.get('decimal_places', 12)}"
    converted = f"try_cast({value} AS DECIMAL(38,12))"
    bad = f"({raw} IS NOT NULL AND ({grouping_bad} OR NOT {valid} OR NOT ({precision}) OR {converted} IS NULL))"
    if kind == "integer":
        bad = f"({bad} OR ({converted} IS NOT NULL AND ({converted} != trunc({converted}) OR try_cast({converted} AS BIGINT) IS NULL)))"
        converted = f"try_cast({converted} AS BIGINT)"
    if field.get('inferred'):
        bad = f"({bad} OR regexp_full_match({raw}, '[+-]?0[0-9]+'))"
    return converted, bad


def export_csv(connection, query, path):
    cursor = connection.execute(query)
    columns = [d[0] for d in cursor.description]
    with open(path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        while rows := cursor.fetchmany(5000):
            writer.writerows([[safe_text(v) for v in row] for row in rows])


def process(recipe, sources, raw_root, decisions, runtime, output, progress=lambda *_: None):
    recipe = validate(recipe, sources)
    runtime, output = Path(runtime), Path(output)
    runtime.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    database = runtime / "data.duckdb"
    con = duckdb.connect(str(database), config={"memory_limit": "1GB", "threads": "2", "enable_external_access": "true"})
    con.execute(f"SET temp_directory={lit(str(runtime / 'spill'))}")
    manifest = {"engine_version": VERSION, "started": now(), "file_issues": [], "tables": [], "views": [],
                "relationship_suggestions": [], "profiles": [], "sources": [{k: s[k] for k in ("id", "filename", "sha256", "settings")} for s in sources]}
    total = 0
    try:
        con.execute("CREATE TABLE decisions(record_id VARCHAR, corrections JSON, excluded BOOLEAN)")
        if decisions:
            con.executemany("INSERT INTO decisions VALUES (?, ?, ?)", [(k, dumps(v.get("corrections", {})), v.get("exclude", False)) for k, v in decisions.items()])
        source_map = {s["id"]: s for s in sources}
        for ti, table in enumerate(recipe["tables"]):
            name, fields = table["name"], table["fields"]
            names = [f["name"] for f in fields]
            raw_table = "raw_" + name
            con.execute(f"CREATE TABLE {q(raw_table)} ({','.join(q(n) + ' VARCHAR' for n in META + names)})")
            for assignment in table["sources"]:
                source = source_map[assignment["id"]]
                progress(f"Reading {source['filename']}", 5 + 30 * ti // max(1, len(recipe["tables"])))
                staged = runtime / (source["id"] + ".csv")
                try:
                    actual_path = Path(raw_root) / source["id"] / source["stored_name"]
                    if file_hash(actual_path) != source["sha256"]:
                        raise WorkbenchError("Saved source bytes changed; add the file again before running.")
                    count = stage_source(actual_path, source, assignment["mapping"], staged, names, MAX_ROWS - total)
                    non_null = '[' + ','.join(lit(n) for n in META + names) + ']'
                    con.execute(f"INSERT INTO {q(raw_table)} SELECT * FROM read_csv({lit(str(staged))}, header=true, all_varchar=true, nullstr='', force_not_null={non_null}, auto_detect=true, delim=',', quote='\"', escape='\"', max_line_size=10485760)")
                    total += count
                except (ValueError, OSError, csv.Error) as exc:
                    if "one-million-row limit" in str(exc):
                        raise WorkbenchError(str(exc)) from exc
                    manifest["file_issues"].append({"source_id": source["id"], "filename": source["filename"], "reason": str(exc), "parsed_rows": None})
                finally:
                    staged.unlink(missing_ok=True)
            progress(f"Cleaning {name}", 40 + 20 * ti // max(1, len(recipe["tables"])))
            corrected = []
            for field in fields:
                n = field["name"]
                corrected.append(f"CASE WHEN json_exists(d.corrections, {lit('$.' + n)}) THEN json_extract_string(d.corrections, {lit('$.' + n)}) ELSE r.{q(n)} END AS {q(n)}")
            con.execute(f"CREATE TABLE {q('corrected_' + name)} AS SELECT {','.join('r.' + q(m) for m in META)}, coalesce(d.excluded,FALSE) AS __excluded, {','.join(corrected)} FROM {q(raw_table)} r LEFT JOIN decisions d ON r.__record_id=d.record_id")
            converted, reasons = [], ["nullif(__ingest_issue,'')"]
            for field in fields:
                n = field["name"]
                default_expression, default_bad = field_expr(field, q(n))
                expressions, invalids = [], []
                for assignment in table['sources']:
                    override = recipe.get('formats', {}).get(assignment.get('format'), {}).get('fields', {}).get(n, {})
                    if override:
                        expr, invalid = field_expr({**field, **override}, q(n))
                        expressions.append(f"WHEN __source_id={lit(assignment['id'])} THEN {expr}")
                        invalids.append(f"WHEN __source_id={lit(assignment['id'])} THEN {invalid}")
                expression = f"CASE {' '.join(expressions)} ELSE {default_expression} END" if expressions else default_expression
                bad = f"CASE {' '.join(invalids)} ELSE {default_bad} END" if invalids else default_bad
                converted.append(f"{expression} AS {q(n)}")
                reasons.append(f"CASE WHEN {bad} THEN {lit(n + ': invalid ' + field['type'] + ' or precision')} END")
                if field.get("required") or n in table.get("key", []):
                    reasons.append(f"CASE WHEN {expression} IS NULL THEN {lit(n + ': required value missing')} END")
                if field.get("allowed"):
                    reasons.append(f"CASE WHEN {expression} IS NOT NULL AND cast({expression} AS VARCHAR) NOT IN ({','.join(lit(v) for v in field['allowed'])}) THEN {lit(n + ': value outside allowed list')} END")
                for attr, op in (("min", "<"), ("max", ">")):
                    if field.get(attr) not in (None, "") and field["type"] in ("integer", "decimal"):
                        reasons.append(f"CASE WHEN {expression} {op} cast({lit(field[attr])} AS DECIMAL(38,12)) THEN {lit(n + ': outside ' + attr + ' bound')} END")
            con.execute(f"CREATE TABLE {q('typed_' + name)} AS SELECT {','.join(q(m) for m in META)}, __excluded, {','.join(converted)}, concat_ws('; ', {','.join(reasons)}) AS __reason FROM {q('corrected_' + name)}")
            for check in table.get('checks', []):
                a, b = q(check['left']), q(check['right'])
                op = {'==': '=', '!=': '<>'}.get(check['op'], check['op'])
                message = check.get('message') or f"Check failed: {check['left']} {check['op']} {check['right']}"
                con.execute(f"UPDATE {q('typed_' + name)} SET __reason=concat_ws('; ',nullif(__reason,''),{lit(message)}) WHERE {a} IS NOT NULL AND {b} IS NOT NULL AND NOT ({a} {op} {b})")
            # Canonical business values drive duplicate checks; provenance remains separate.
            columns = ",".join(q(n) for n in names)
            con.execute(f"CREATE TABLE {q('marked_' + name)} AS SELECT *, md5(to_json(struct_pack({','.join(q(n) + ' := ' + q(n) for n in names)}))) AS __value_hash, row_number() OVER (PARTITION BY {columns} ORDER BY __source_name,__row::BIGINT,__record_id) AS __copy, count(*) OVER (PARTITION BY {columns}) AS __copies FROM {q('typed_' + name)}")
            # Explicit exclusions do not poison an otherwise resolved key or duplicate group.
            con.execute(f"CREATE TABLE {q('groups_' + name)} AS SELECT {columns}, count(*) AS __active_copies FROM {q('marked_' + name)} WHERE NOT __excluded GROUP BY {columns}")
            if table.get("key"):
                keys = ",".join(q(k) for k in table["key"])
                con.execute(f"CREATE TABLE {q('keys_' + name)} AS SELECT {keys}, count(*) AS __key_count, count(DISTINCT __value_hash) AS __variants, bool_or(__reason != '') AS __key_invalid FROM {q('marked_' + name)} WHERE NOT __excluded GROUP BY {keys}")
                key_join = " AND ".join(f"m.{q(k)} IS NOT DISTINCT FROM k.{q(k)}" for k in table["key"])
                key_reason = "CASE WHEN k.__variants > 1 OR (k.__key_count > 1 AND k.__key_invalid) THEN 'Conflicting or invalid copies of key' END"
                key_sql = f"LEFT JOIN {q('keys_' + name)} k ON {key_join}"
            else:
                key_reason, key_sql = "NULL", ""
            policy = table.get("duplicate_policy", "flag")
            dup_reason = "CASE WHEN __active_copies > 1 THEN 'Repeated record: choose a duplicate policy or exclude a copy' END" if policy == "flag" else "NULL"
            group_join = " AND ".join(f"m.{q(n)} IS NOT DISTINCT FROM g.{q(n)}" for n in names)
            con.execute(f"CREATE TABLE {q('result_' + name)} AS SELECT m.*, concat_ws('; ', nullif(m.__reason,''), {key_reason}, {dup_reason}) AS __issues FROM {q('marked_' + name)} m LEFT JOIN {q('groups_' + name)} g ON {group_join} {key_sql}")
            # Recalculate repeat rank among non-excluded records, after all decisions.
            con.execute(f"ALTER TABLE {q('result_' + name)} ADD COLUMN __status VARCHAR")
            con.execute(f"UPDATE {q('result_' + name)} SET __status=CASE WHEN __excluded THEN 'excluded' WHEN __issues!='' THEN 'review' ELSE 'accepted' END")
            if policy == "drop_exact":
                con.execute(f"UPDATE {q('result_' + name)} SET __status='excluded', __issues='Exact repeat excluded by saved policy' WHERE __record_id IN (SELECT __record_id FROM (SELECT __record_id, row_number() OVER (PARTITION BY {columns} ORDER BY __source_name,__row::BIGINT,__record_id) AS n FROM {q('result_' + name)} WHERE __status='accepted') WHERE n>1)")
            con.execute(f"UPDATE {q('result_' + name)} SET __issues='Excluded by recorded review decision' WHERE __excluded")
            con.execute(f"CREATE TABLE {q(name)} AS SELECT {','.join(q(n) for n in names + META[:5])} FROM {q('result_' + name)} WHERE __status='accepted'")
            counts = dict(con.execute(f"SELECT __status,count(*) FROM {q('result_' + name)} GROUP BY __status").fetchall())
            totals = {}
            for f in fields:
                if f["type"] in ("decimal", "integer"):
                    value, populated = con.execute(f"SELECT sum({q(f['name'])}),count({q(f['name'])}) FROM {q(name)}").fetchone()
                    totals[f["name"]] = {"sum": str(value) if value is not None else None, "populated": populated}
            manifest["tables"].append({"name": name, "input": sum(counts.values()), "accepted": counts.get("accepted", 0), "review": counts.get("review", 0), "excluded": counts.get("excluded", 0), "totals": totals})
            for field in fields:
                n = field["name"]
                missing, distinct = con.execute(f"SELECT count(*) FILTER (WHERE {q(n)} IS NULL),count(DISTINCT {q(n)}) FROM {q('result_' + name)}").fetchone()
                profile = {"table": name, "field": n, "type": field["type"], "missing": missing, "distinct": distinct, "rows": sum(counts.values())}
                if field["type"] in ("integer", "decimal"):
                    low, high, q1, q3 = con.execute(f"SELECT min({q(n)}),max({q(n)}),quantile_cont({q(n)},0.25),quantile_cont({q(n)},0.75) FROM {q(name)}").fetchone()
                    profile.update(min=str(low) if low is not None else None, max=str(high) if high is not None else None, outliers=0)
                    if q1 is not None and q3 is not None and q3 > q1:
                        lower, upper = float(q1) - (float(q3) - float(q1)) * 1.5, float(q3) + (float(q3) - float(q1)) * 1.5
                        profile["outliers"] = con.execute(f"SELECT count(*) FROM {q(name)} WHERE {q(n)} < ? OR {q(n)} > ?", [lower, upper]).fetchone()[0]
                    profile["outlier_method"] = "Outside 1.5 × IQR among accepted values; diagnostic only, no exclusion or imputation."
                manifest["profiles"].append(profile)
            manifest["tables"][-1]["formula_rows"] = con.execute(f"SELECT count(*) FROM {q('result_' + name)} WHERE contains(__formulas, '\"formula\":')").fetchone()[0]
            export_csv(con, f"SELECT * FROM {q(name)} ORDER BY __source_name,__row::BIGINT", output / f"{name}.csv")
            for status in ("review", "excluded"):
                export_csv(con, f"SELECT {','.join(q(n) for n in names + META)}, __issues, __status FROM {q('result_' + name)} WHERE __status={lit(status)} ORDER BY __source_name,__row::BIGINT", output / f"{name}-{status}.csv")
            for prefix in ("raw_", "corrected_", "typed_", "marked_", "groups_", "keys_"):
                con.execute(f"DROP TABLE IF EXISTS {q(prefix + name)}")
        progress("Checking relationships", 65)
        manifest["relationship_suggestions"] = suggest_relationships(con, recipe)
        for view in recipe.get("views", []):
            manifest["views"].append(build_view(con, recipe, view, output=output))
            export_csv(con, f"SELECT * FROM {q(view['name'])}", output / (view["name"] + ".csv"))
        export_csv(con, " UNION ALL ".join(f"SELECT {lit(t['name'])} AS table_name, __source_id, __source_name, __sheet, __row, __record_id, __raw, __formulas, to_json(struct_pack({','.join(q(f['name']) + ' := ' + q(f['name']) for f in t['fields'])})) AS cleaned_values, __issues, __status FROM {q('result_' + t['name'])}" for t in recipe["tables"]), output / "audit.csv")
        dictionary = [{"table": t["name"], **f} for t in recipe["tables"] for f in t["fields"]]
        write_json(output / "data-dictionary.json", dictionary)
        write_json(output / "recipe.json", recipe)
        write_json(output / "decisions.json", decisions)
        manifest["input_rows"] = total
        manifest["complete_inputs"] = not manifest["file_issues"]
        manifest["review_rows"] = sum(t["review"] for t in manifest["tables"])
        manifest["status"] = "incomplete" if manifest["file_issues"] else "needs_review" if manifest["review_rows"] else "ready"
        progress("Writing review workbook", 80)
        from .exports import write_workbook
        write_workbook(con, recipe, manifest, output / "Review.xlsx")
        con.execute("CHECKPOINT")
    finally:
        con.close()
    shutil.copyfile(database, output / "data.duckdb")
    manifest["finished"] = now()
    return manifest


def suggest_relationships(con, recipe):
    suggestions = []
    for left in recipe["tables"]:
        for right in recipe["tables"]:
            if left["name"] == right["name"]:
                continue
            right_fields = {f["name"]: f for f in right["fields"]}
            for field in left["fields"]:
                n = field["name"]
                if n not in right_fields or right_fields[n]["type"] != field["type"]:
                    continue
                if not (n.endswith("_id") or n in right.get("key", [])):
                    continue
                populated, unique = con.execute(f"SELECT count({q(n)}),count(DISTINCT {q(n)}) FROM {q(right['name'])}").fetchone()
                matched, base_rows = con.execute(f"SELECT count(*) FILTER (WHERE l.{q(n)} IS NOT NULL AND EXISTS (SELECT 1 FROM {q(right['name'])} r WHERE r.{q(n)}=l.{q(n)})),count(*) FROM {q(left['name'])} l").fetchone()
                if not matched:
                    continue
                suggestions.append({"base": left["name"], "lookup": right["name"], "field": n,
                                    "lookup_unique": populated == unique, "lookup_populated": populated,
                                    "matched": matched, "base_rows": base_rows,
                                    "reason": "Matching heading and type; exact value overlap checked on all accepted rows. Approval required."})
    return suggestions


def build_view(con, recipe, view, materialize=True, output=None):
    base, name = view["base"], view["name"]
    columns = ["b.*"]
    joins, reports = [], []
    before = con.execute(f"SELECT count(*) FROM {q(base)}").fetchone()[0]
    table_map = {t["name"]: t for t in recipe["tables"]}
    base_types = {f["name"]: f["type"] for f in table_map[base]["fields"]}
    for index, join in enumerate(view.get("joins", [])):
        lookup, alias = join["table"], "j" + str(index)
        lk, rk = join["left_on"], join["right_on"]
        lookup_types = {f["name"]: f["type"] for f in table_map[lookup]["fields"]}
        if any(base_types[a] != lookup_types[b] for a, b in zip(lk, rk)):
            raise WorkbenchError("Join key types differ. Normalize them explicitly before joining.")
        aggregate = join.get("aggregations", [])
        if aggregate:
            if len({a["name"] for a in aggregate}) != len(aggregate):
                raise WorkbenchError("Aggregation output names must be unique.")
            expression = ",".join(q(k) for k in rk) + "," + ",".join(f"{a['op']}({q(a['field'])}) AS {q(a['name'])}" for a in aggregate)
            query = f"SELECT {expression} FROM {q(lookup)} GROUP BY {','.join(q(k) for k in rk)}"
            out_fields = rk + [a["name"] for a in aggregate]
        else:
            query = f"SELECT * FROM {q(lookup)}"
            out_fields = [f["name"] for f in table_map[lookup]["fields"]]
        valid_key = " AND ".join(f"{q(k)} IS NOT NULL" for k in rk)
        duplicate_groups = con.execute(f"SELECT count(*) FROM (SELECT {','.join(q(k) for k in rk)} FROM ({query}) WHERE {valid_key} GROUP BY {','.join(q(k) for k in rk)} HAVING count(*)>1)").fetchone()[0]
        if duplicate_groups:
            raise WorkbenchError(f"Join blocked: {lookup} contains {duplicate_groups} repeated lookup keys. Aggregate the child table or resolve duplicates first.")
        condition = " AND ".join(f"b.{q(a)}={alias}.{q(b)}" for a, b in zip(lk, rk))
        unmatched = con.execute(f"SELECT count(*) FROM {q(base)} b LEFT JOIN ({query}) {alias} ON {condition} WHERE {alias}.{q(rk[0])} IS NULL").fetchone()[0]
        orphan_condition = " AND ".join(f"b.{q(a)}=r.{q(b)}" for a, b in zip(lk, rk))
        orphan_query = f"SELECT r.* FROM {q(lookup)} r WHERE NOT EXISTS (SELECT 1 FROM {q(base)} b WHERE {orphan_condition})"
        orphan_count = con.execute(f"SELECT count(*) FROM ({orphan_query})").fetchone()[0]
        if materialize and output:
            export_csv(con, f"SELECT b.* FROM {q(base)} b LEFT JOIN ({query}) {alias} ON {condition} WHERE {alias}.{q(rk[0])} IS NULL", Path(output) / f"{name}-unmatched-base-{lookup}.csv")
            export_csv(con, orphan_query, Path(output) / f"{name}-unmatched-lookup-{lookup}.csv")
        reports.append({"lookup": lookup, "how": join.get("how", "left"), "base_rows": before, "unmatched_base_rows": unmatched, "unmatched_lookup_rows": orphan_count, "aggregated": bool(aggregate), "predicted_rows_if_only_join": before if join.get("how", "left") == "left" else before - unmatched})
        joins.append(f"{join.get('how', 'left').upper()} JOIN ({query}) {alias} ON {condition}")
        columns.extend(f"{alias}.{q(f)} AS {q(lookup + '__' + f)}" for f in out_fields)
    query = f"SELECT {','.join(columns)} FROM {q(base)} b {' '.join(joins)}"
    after = con.execute(f"SELECT count(*) FROM ({query})").fetchone()[0]
    if after > before:
        raise WorkbenchError("Join would multiply base records; choose unique lookup keys or aggregate first.")
    if materialize:
        con.execute(f"CREATE TABLE {q(name)} AS {query}")
    return {"name": name, "base": base, "before": before, "after": after, "joins": reports}
