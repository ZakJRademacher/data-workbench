"""Fictional business fixtures with independently specified expected results."""
import csv
import tempfile
from pathlib import Path

from openpyxl import Workbook


def create_demo(store):
    project = store.create("Northstar · Customer & order reconciliation")
    pid = project["id"]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        customers = root / "customers.csv"
        with customers.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerows([
                ["Customer ID", "Customer Name", "Region"],
                ["001", " Mira Chen ", "West"], ["002", "Omar Patel", "E"],
                ["003", "June Brooks", "West"], ["003", "June B.", "East"],
                ["004", "Alex Rivera", "North"], ["005", "Sam Taylor", "South"],
            ])
        orders = root / "orders_jan.csv"
        with orders.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows([
                ["Order ID", "Cust ID", "Order Date", "Order Total"],
                ["O100", "001", "2026-01-03", "125.5000"],
                ["O101", "002", "2026-01-04", "75.25"],
                ["O102", "003", "01/05/2026", "90.00"],
                ["O103", "009", "2026-01-06", "60"],
                ["O104", "004", "2026-01-07", "bad"],
            ])
        workbook = Workbook()
        workbook.properties.creator = "Zak Rademacher"
        ws = workbook.active
        ws.title = "Orders"
        ws.append(["Northstar February export · fictional"])
        ws.append(["Order ID", "Customer ID", "Order Date", "Amount"])
        ws.append(["O105", "004", "2026-02-01", "200.10"])
        ws.append(["O106", "005", "2026-02-02", "45.1250"])
        ws.append(["O106", "005", "2026-02-02", "45.1250"])
        workbook.save(root / "orders_feb.xlsx")
        payments = root / "payments.csv"
        with payments.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows([
                ["Payment ID", "Order ID", "Paid Amount"],
                ["P1", "O100", "100.00"], ["P2", "O100", "25.50"],
                ["P3", "O101", "75.25"], ["P4", "O105", "200.10"],
                ["P5", "O999", "10.00"], ["P6", "O106", "45.1250"],
            ])
        for path in [customers, orders, root / "orders_feb.xlsx", payments]:
            store.add_source(pid, path.name, path)
    recipe = store.set_recipe(pid)
    for t in recipe["tables"]:
        t["key"] = [{"customers": "customer_id", "orders": "order_id", "payments": "payment_id"}[t["name"]]]
        for f in t["fields"]:
            if f["name"] in t["key"]:
                f["required"] = True
            if f["name"] in ("amount", "paid_amount"):
                f["type"] = "decimal"
            if f["name"] == "order_date":
                f["type"] = "date"
            if f["name"] == "region":
                f["mapping"] = {"E": "East"}
    store.set_recipe(pid, recipe)
    return store.get(pid)


EXPECTED = {
    "customers": {"input": 6, "accepted": 4, "review": 2, "excluded": 0},
    "orders": {"input": 8, "accepted": 4, "review": 4, "excluded": 0, "amount_total": "460.850000000000"},
    "payments": {"input": 6, "accepted": 6, "review": 0, "excluded": 0, "paid_amount_total": "455.975000000000"},
}
