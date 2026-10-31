from decimal import Decimal
import math

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .common import WORKBOOK_ROWS, q, safe_text


def write_workbook(con, recipe, manifest, path):
    book = Workbook(write_only=True)
    book.properties.creator = "Zak Rademacher"
    book.properties.lastModifiedBy = "Zak Rademacher"
    book.properties.title = "Data Workbench Â· Project review"
    book.properties.description = "Source reconciliation and labeled previews. Complete records are in the accompanying CSV files."

    def sheet(title, headers, rows, note):
        ws = book.create_sheet(title[:31])
        ws.freeze_panes = "A4"
        ws.sheet_view.showGridLines = False
        for i, header in enumerate(headers, 1):
            special = {"Record ID": 36, "Reason": 64, "Raw values": 85, "Source": 25, "Sheet": 18, "Record / row": 16}
            ws.column_dimensions[get_column_letter(i)].width = special.get(header, min(48, max(22, len(str(header)) + 3)))
        title_cell = WriteOnlyCell(ws, value=title)
        title_cell.font = Font(name="Arial", size=16, bold=True, color="173562")
        ws.row_dimensions[1].height = 32
        ws.merged_cells.add(f"A1:{get_column_letter(max(2, min(len(headers), 6)))}1")
        ws.append([title_cell])
        note_cell = WriteOnlyCell(ws, value=note)
        note_cell.alignment = Alignment(wrap_text=True, vertical="center")
        note_cell.font = Font(name="Arial", size=10, color="586B89")
        ws.merged_cells.add(f"A2:{get_column_letter(max(2, min(len(headers), 6)))}2")
        ws.append([note_cell])
        ws.row_dimensions[2].height = 48
        ws.row_dimensions[3].height = 30
        cells = []
        for h in headers:
            cell = WriteOnlyCell(ws, value=h)
            cell.fill = PatternFill("solid", fgColor="2458C8")
            cell.font = Font(name="Arial", bold=True, color="FFFFFF")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cells.append(cell)
        ws.append(cells)
        count = 0
        for row in rows:
            count += 1
            cells = []
            max_lines = 1
            for col, value in enumerate(row, 1):
                exact_decimal = isinstance(value, Decimal)
                if isinstance(value, Decimal):
                    value = str(value)
                if isinstance(value, (dict, list)):
                    value = str(value)
                cell = WriteOnlyCell(ws, value=safe_text(value))
                cell.font = Font(name="Arial", size=11, color="172B49")
                width = ws.column_dimensions[get_column_letter(col)].width
                lines = sum(max(1, math.ceil(len(part) / max(1, width - 2))) for part in str(value or '').split('\n'))
                max_lines = max(max_lines, lines)
                cell.alignment = Alignment(vertical="top", wrap_text=True, horizontal="right" if exact_decimal or isinstance(value, (int, float)) else "left")
                if isinstance(value, str):
                    cell.number_format = '@'
                elif isinstance(value, int):
                    cell.number_format = '#,##0'
                if count % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="EFF4FC")
                cells.append(cell)
            ws.row_dimensions[count + 3].height = max(21, min(180, 16 * max_lines))
            ws.append(cells)
        ws.auto_filter.ref = f"A3:{get_column_letter(len(headers))}{max(3, count + 3)}"
        return ws

    sheet("Project summary", ["Table", "Input rows", "Accepted", "Needs review", "Excluded"],
          [[t[k] for k in ("name", "input", "accepted", "review", "excluded")] for t in manifest["tables"]],
          f"Status: {manifest['status']}. Parsed input rows = accepted + review + excluded. Unreadable files have unknown counts.")
    totals = [[t["name"], field, value["sum"], value["populated"]] for t in manifest["tables"] for field, value in t["totals"].items()]
    sheet("Accepted totals", ["Table", "Field", "Exact total (text)", "Populated accepted rows"], totals,
          "Exact decimal totals are text to preserve precision. Units and currencies are not inferred. Blank total means no populated values.")
    sheet("File issues", ["File", "Issue", "Parsed rows"],
          [[i["filename"], i["reason"], "Unknown"] for i in manifest["file_issues"]],
          "A file issue makes the project incomplete. Accepted outputs cover successfully parsed files only.")
    for t in recipe["tables"]:
        cursor = con.execute(f"SELECT __source_name,__sheet,__row,__record_id,__issues,__raw FROM {q('result_' + t['name'])} WHERE __status='review' ORDER BY __source_name,__row::BIGINT LIMIT {WORKBOOK_ROWS}")
        sheet("Review " + t["name"][:20], ["Source", "Sheet", "Record / row", "Record ID", "Reason", "Raw values"], cursor.fetchall(),
              f"PREVIEW: first {WORKBOOK_ROWS:,} review records maximum. Complete review CSV and audit.csv accompany this workbook.")
        fields = [f["name"] for f in t["fields"]]
        cursor = con.execute(f"SELECT {','.join(q(f) for f in fields)} FROM {q(t['name'])} ORDER BY __source_name,__row::BIGINT LIMIT {WORKBOOK_ROWS}")
        sheet("Clean " + t["name"][:21], fields, cursor.fetchall(),
              f"PREVIEW: first {WORKBOOK_ROWS:,} accepted records maximum. Complete clean data is in {t['name']}.csv. Decimal values are exact text.")
    sheet("Relationships", ["View", "Base", "Before", "After", "Join details"],
          [[v[k] for k in ("name", "base", "before", "after", "joins")] for v in manifest["views"]],
          "Combined views retain the selected base grain. Unmatched lookup records and aggregation are reported in the manifest.")
    book.save(path)
