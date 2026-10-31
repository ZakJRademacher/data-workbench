from __future__ import annotations

import copy
import re
from decimal import Decimal, InvalidOperation

from .common import MAX_COLUMNS, WorkbenchError, identifier

ALIASES = {"cust_id": "customer_id", "customer_number": "customer_id", "customer_no": "customer_id",
           "order_number": "order_id", "order_no": "order_id", "payment_number": "payment_id",
           "customer_name": "name", "sale_amount": "amount", "order_total": "amount"}
TYPES = {"text", "integer", "decimal", "date"}
DATE_FORMATS = {"%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y", "%Y/%m/%d"}
FORMAT_FIELDS = {'date_format', 'date_exact', 'decimal_separator', 'thousands_separator', 'strip_symbols'}


def migrate(recipe):
    if not isinstance(recipe, dict) or recipe.get('version') not in (1, 2):
        raise WorkbenchError('Use a supported version 1 or version 2 recipe.')
    result = copy.deepcopy(recipe)
    result['version'] = 2
    result.setdefault('formats', {})
    for table in result.get('tables', []):
        table.setdefault('checks', [])
    return result


def canonical(header):
    name = identifier(header)
    return ALIASES.get(name, name)


def starter_recipe(sources):
    recipe = {"version": 2, "tables": [], "views": [], "formats": {}}
    groups = {}
    for source in sources:
        names = [canonical(h) for h in source["headers"]]
        if len(set(names)) != len(names):
            names = [f["name"] for f in source["fields"]]
        signature = tuple(sorted(names))
        if signature not in groups:
            name = identifier(re.sub(r"(?:[_ -]?(?:\d{4}.*|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))$", "", source["filename"].rsplit(".", 1)[0], flags=re.I))
            while name in {t["name"] for t in recipe["tables"]}:
                name += "_2"
            fields = [dict(copy.deepcopy(f), name=n) for n, f in zip(names, source["fields"])]
            table = {"name": name, "fields": fields, "sources": [], "key": [], "duplicate_policy": "flag", "checks": []}
            groups[signature] = table
            recipe["tables"].append(table)
        table = groups[signature]
        table["sources"].append({"id": source["id"], "mapping": dict(zip(names, source["headers"])), "ignored_columns": []})
    return recipe


def _validate(recipe, sources):
    if not isinstance(recipe, dict) or recipe.get("version") != 2:
        raise WorkbenchError("Use a version 2 recipe object.")
    if not isinstance(recipe.get('formats', {}), dict):
        raise WorkbenchError('Source formats must be a named object.')
    for fmt_name, fmt in recipe.get('formats', {}).items():
        if not isinstance(fmt_name, str) or not 1 <= len(fmt_name) <= 100 or not isinstance(fmt.get('fields', {}), dict):
            raise WorkbenchError('Each named format needs a fields object.')
        for settings in fmt.get('fields', {}).values():
            if not isinstance(settings, dict) or not set(settings).issubset(FORMAT_FIELDS):
                raise WorkbenchError('Source formats may override date and number parsing only.')
            if settings.get('date_format', '%Y-%m-%d') not in DATE_FORMATS:
                raise WorkbenchError('Unknown source date format.')
            if settings.get('decimal_separator', '.') not in ('.', ',') or settings.get('thousands_separator', '') not in ('', '.', ',', ' '):
                raise WorkbenchError('Unsupported source number separators.')
            if 'date_exact' in settings and not isinstance(settings['date_exact'], bool):
                raise WorkbenchError('Exact date width must be true or false.')
    if not isinstance(recipe.get("tables"), list) or not recipe["tables"]:
        raise WorkbenchError("Create at least one logical table.")
    source_map = {s["id"]: s for s in sources}
    tables, assigned = {}, set()
    for table in recipe["tables"]:
        name = table.get("name", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", name) or name.startswith(("raw_", "corrected_", "typed_", "marked_", "groups_", "keys_", "result_")) or name == "decisions" or name in tables:
            raise WorkbenchError("Table names must be unique lowercase identifiers, up to 60 characters.")
        tables[name] = table
        fields = table.get("fields", [])
        if not 1 <= len(fields) <= MAX_COLUMNS:
            raise WorkbenchError(f"Each table needs 1–{MAX_COLUMNS} fields.")
        names = set()
        for field in fields:
            fname = field.get("name", "")
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", fname) or fname in names:
                raise WorkbenchError("Field names must be unique lowercase identifiers, up to 60 characters.")
            names.add(fname)
            if field.get("type") not in TYPES:
                raise WorkbenchError("Field types are Text, Whole number, Decimal, or Date.")
            if not isinstance(field.get('decimal_places', 12), int) or not 0 <= field.get('decimal_places', 12) <= 12:
                raise WorkbenchError('Decimal places must be an integer between 0 and 12.')
            if field.get('precision_mode', 'nonzero') not in ('strict', 'nonzero'):
                raise WorkbenchError('Precision mode must be strict or nonzero.')
            for attr in ('case_insensitive', 'date_exact', 'unsigned', 'inferred'):
                if attr in field and not isinstance(field[attr], bool):
                    raise WorkbenchError(f'{attr} must be true or false.')
            if field.get("date_format", "%Y-%m-%d") not in DATE_FORMATS:
                raise WorkbenchError("Choose a supported explicit date format.")
            if field.get("decimal_separator", ".") not in (".", ",") or field.get("thousands_separator", "") not in ("", ".", ",", " "):
                raise WorkbenchError("Choose supported numeric separators.")
            if field.get("decimal_separator", ".") == field.get("thousands_separator", ""):
                raise WorkbenchError("Decimal and thousands separators must differ.")
            for attr in ("missing", "allowed"):
                if not isinstance(field.get(attr, []), list) or not all(isinstance(v, str) for v in field.get(attr, [])):
                    raise WorkbenchError(f"{attr} must be a list of text values.")
            if not isinstance(field.get("mapping", {}), dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in field.get("mapping", {}).items()):
                raise WorkbenchError("Category mappings must contain text keys and values.")
            if field.get('case_insensitive'):
                for values in (field.get('allowed', []), list(field.get('mapping', {}))):
                    if len({v.lower() for v in values}) != len(values):
                        raise WorkbenchError('Case-insensitive category rules contain ambiguous repeated values.')
            for attr in ("min", "max"):
                if field.get(attr) not in (None, ""):
                    try:
                        bound = Decimal(str(field[attr]))
                        if not bound.is_finite() or abs(bound) >= Decimal('1e26') or bound.normalize().as_tuple().exponent < -12:
                            raise InvalidOperation
                    except (InvalidOperation, ValueError):
                        raise WorkbenchError("Numeric bounds must be finite numbers.")
            if field.get('min') not in (None, '') and field.get('max') not in (None, '') and Decimal(str(field['min'])) > Decimal(str(field['max'])):
                raise WorkbenchError("Minimum cannot exceed maximum.")
        if not set(table.get("key", [])).issubset(names):
            raise WorkbenchError("A key references a missing field.")
        types = {f['name']: f['type'] for f in fields}
        for check in table.get('checks', []):
            a, b, op = check.get('left'), check.get('right'), check.get('op')
            if a not in names or b not in names or op not in ('>=', '<=', '>', '<', '==', '!='):
                raise WorkbenchError('Cross-field checks need existing left/right fields and an allowlisted comparison.')
            if types[a] != types[b] and not {types[a], types[b]}.issubset({'integer', 'decimal'}):
                raise WorkbenchError('Cross-field checks need compatible field types.')
        if table.get("duplicate_policy", "flag") not in ("flag", "keep_all", "drop_exact"):
            raise WorkbenchError("Choose Flag repeats, Keep all, or Exclude exact repeats.")
        if not table.get("sources"):
            raise WorkbenchError(f"Table {name} has no source files.")
        for assignment in table["sources"]:
            sid = assignment.get("id")
            if sid not in source_map or sid in assigned:
                raise WorkbenchError("Assign every source to exactly one table.")
            assigned.add(sid)
            mapping = assignment.get("mapping", {})
            fmt = assignment.get('format')
            if fmt and fmt not in recipe.get('formats', {}):
                raise WorkbenchError('A source references a missing named format.')
            overrides = recipe.get('formats', {}).get(fmt, {}).get('fields', {})
            if not set(overrides).issubset(names):
                raise WorkbenchError('A source format references a missing output field.')
            for f in fields:
                merged = {**f, **overrides.get(f['name'], {})}
                if merged.get('decimal_separator', '.') == merged.get('thousands_separator', ''):
                    raise WorkbenchError('Source decimal and thousands separators must differ.')
            headers = set(source_map[sid]["headers"])
            if not set(mapping).issubset(names) or any(v not in headers for v in mapping.values() if v):
                raise WorkbenchError("A source mapping references a missing source heading or output field.")
            ignored = set(assignment.get("ignored_columns", []))
            if not ignored.issubset(headers) or headers - set(mapping.values()) - ignored:
                raise WorkbenchError("Map every source column or explicitly list it under ignored_columns.")
            if any(not mapping.get(k) for k in table.get("key", [])):
                raise WorkbenchError("Every source must map the table's key fields.")
    if assigned != set(source_map):
        raise WorkbenchError("Some project sources have not been assigned to a table. Regenerate or update the recipe.")
    view_names = set(tables)
    for view in recipe.get("views", []):
        name = view.get("name", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", name) or name in view_names:
            raise WorkbenchError("Combined view names must be unique lowercase identifiers.")
        view_names.add(name)
        if view.get("base") not in tables:
            raise WorkbenchError("Choose an existing base table for each view.")
        base_fields = {f["name"] for f in tables[view["base"]]["fields"]}
        used = {view["base"]}
        for join in view.get("joins", []):
            if join.get("table") not in tables or join["table"] in used:
                raise WorkbenchError("Choose a different existing lookup table for each join.")
            used.add(join["table"])
            if join.get("how", "left") not in ("left", "inner"):
                raise WorkbenchError("Join type must be left or inner.")
            keys, right_keys = join.get("left_on", []), join.get("right_on", [])
            right_fields = {f["name"]: f for f in tables[join["table"]]["fields"]}
            if not keys or len(keys) != len(right_keys) or not set(keys).issubset(base_fields) or not set(right_keys).issubset(right_fields):
                raise WorkbenchError("Join keys must reference corresponding base and lookup fields.")
            for agg in join.get("aggregations", []):
                if agg.get("op") not in ("count", "sum", "min", "max") or agg.get("field") not in right_fields:
                    raise WorkbenchError("Aggregations support count, sum, min and max on existing fields.")
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", agg.get("name", "")) or agg["name"] in right_keys:
                    raise WorkbenchError("Aggregation output names must be identifiers distinct from join keys.")
                if agg["op"] == "sum" and right_fields[agg["field"]]["type"] not in ("integer", "decimal"):
                    raise WorkbenchError("Sum requires a numeric field.")
    return recipe


def validate(recipe, sources):
    try:
        return _validate(migrate(recipe), sources)
    except (TypeError, KeyError, AttributeError) as exc:
        raise WorkbenchError("Malformed recipe. Check table, field, mapping, and relationship structures against an exported recipe.") from exc
