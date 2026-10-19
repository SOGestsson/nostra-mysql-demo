#!/usr/bin/env python3
"""Create the Goa test database and load the item, stock, sales, and class files.

Reads structure from smart_stock and copies no rows from it. Refuses the live
purchasing databases. Item links and markaðsinngrip are not in these files.
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import sys
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

import bcrypt
import mysql.connector

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.m7_schema import CREATE_TABLES, LiveDatabaseRefused, assert_m7_database_allowed, create_m7_tables

LOGICAL_NAME = "goa"
PHYSICAL_NAME = "goa"
SOURCE_SCHEMA = "smart_stock"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

CLASS_ROWS = [
    ("A", "A", "exclude", 1, 1, "Ekki spáð. Notar markaðsinngrip."),
    ("B", "B", "forecast", 1, 1, ""),
    ("C", "C", "forecast", 1, 1, ""),
    ("D", "D", "exclude", 1, 0, ""),
    ("E", "E", "forecast", 1, 1, ""),
    ("F", "F", "exclude", 1, 0, ""),
    ("G", "G", "exclude", 1, 0, ""),
]


def _password() -> str:
    password = os.getenv("MASTER_DB_PASSWORD", "").strip()
    if password:
        return password
    text = (ROOT / "app" / "db.py").read_text()
    marker = 'MASTER_DB_PASSWORD", "'
    start = text.find(marker)
    if start < 0:
        raise SystemExit("MASTER_DB_PASSWORD is required")
    start += len(marker)
    end = text.find('"', start)
    return text[start:end]


def connect(database: str | None = None):
    return mysql.connector.connect(
        host=os.getenv("MASTER_DB_HOST", "100.108.73.62"),
        port=int(os.getenv("MASTER_DB_PORT", "4406")),
        user=os.getenv("MASTER_DB_USER", "root"),
        password=_password(),
        database=database,
        connection_timeout=20,
    )


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.upper() == "NULL":
        return None
    return text


def _num(value: str | None):
    text = _blank(value)
    if text is None:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def read_tsv(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def read_xlsx_rows(path: pathlib.Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", NS):
                shared.append("".join((node.text or "") for node in item.findall(".//m:t", NS)))
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in sheet.findall("m:sheetData/m:row", NS):
            values: list[str] = []
            for cell in row.findall("m:c", NS):
                kind = cell.attrib.get("t")
                node = cell.find("m:v", NS)
                text = ""
                if node is not None and node.text:
                    text = shared[int(node.text)] if kind == "s" else node.text
                values.append(text)
            rows.append(values)
    return rows


def class_map(folder: pathlib.Path) -> dict[str, str]:
    found: dict[str, str] = {}
    voru = folder / "GOA_Vorumaster_Helgi_16092026.xlsx"
    rows = read_xlsx_rows(voru)
    header = rows[0]
    number_at = header.index("Vörunúmer")
    class_at = header.index("Spástuðull")
    for row in rows[1:]:
        if len(row) <= max(number_at, class_at):
            continue
        item = _blank(row[number_at])
        code = _blank(row[class_at])
        if item and code:
            found[item] = code.upper()
    older = folder / "GOA_itemList_PALMI.xlsx"
    rows = read_xlsx_rows(older)
    header = rows[0]
    number_at = header.index("Vörunúmer")
    class_at = header.index("Spástuðull")
    for row in rows[1:]:
        if len(row) <= max(number_at, class_at):
            continue
        item = _blank(row[number_at])
        code = _blank(row[class_at])
        if item and code and item not in found:
            found[item] = code.upper()
    return found


def item_types(folder: pathlib.Path) -> dict[str, str]:
    rows = read_xlsx_rows(folder / "GOA_itemList_PALMI.xlsx")
    header = rows[0]
    number_at = header.index("Vörunúmer")
    type_at = header.index("Gerð afurðar")
    found: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) <= max(number_at, type_at):
            continue
        item = _blank(row[number_at])
        kind = _blank(row[type_at])
        if item and kind:
            found[item] = kind
    return found


def copy_structure(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
            (PHYSICAL_NAME,),
        )
        if cursor.fetchone():
            raise SystemExit(f"{PHYSICAL_NAME} already exists. Refusing to replace it.")
        cursor.execute(
            f"CREATE DATABASE `{PHYSICAL_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME",
            (SOURCE_SCHEMA,),
        )
        tables = [row[0] for row in cursor.fetchall()]
        for table in tables:
            cursor.execute(
                f"CREATE TABLE `{PHYSICAL_NAME}`.`{table}` LIKE `{SOURCE_SCHEMA}`.`{table}`"
            )
        cursor.execute(
            f"ALTER TABLE `{PHYSICAL_NAME}`.`item_histories` "
            "MODIFY `qty` DECIMAL(18, 4) NULL"
        )
    conn.commit()


def load(conn, folder: pathlib.Path) -> dict[str, int]:
    items = read_tsv(folder / "Itemlist_goa.txt")
    stock = {row["ItemId"].strip(): row for row in read_tsv(folder / "currentstock_goa.txt")}
    classes = class_map(folder)
    types = item_types(folder)
    counts = {"items": 0, "sales": 0, "classes": 0}
    create_m7_tables(conn)
    with conn.cursor() as cursor:
        for row in items:
            item_number = _blank(row.get("ItemId"))
            if not item_number:
                continue
            levels = stock.get(item_number, {})
            cursor.execute(
                f"""
                INSERT INTO `{PHYSICAL_NAME}`.items (
                    vendor_name, price, stock_level, qty_on_order, product_group_name,
                    item_number, description, del_time, moq, order_multiple,
                    innkaupum_haett, item_type, organisation, service_level
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    _blank(row.get("VendorName")),
                    _num(row.get("CostPrice")),
                    _num(levels.get("AvailablePhysical")),
                    _num(levels.get("OnOrder")),
                    _blank(row.get("ItemGroup")),
                    item_number,
                    _blank(row.get("ItemName")) or item_number,
                    _num(row.get("PurchaseLeadTimeDays")),
                    _num(row.get("MOQ")),
                    _num(row.get("OrderMultiple")),
                    _blank(row.get("PurchaseStopped")),
                    types.get(item_number),
                    "goa",
                    0.95,
                ),
            )
            counts["items"] += 1
        cursor.execute(f"SELECT id, item_number FROM `{PHYSICAL_NAME}`.items")
        ids = {str(number): item_id for item_id, number in cursor.fetchall()}
        sales_rows = []
        for row in read_tsv(folder / "ItemIdYearMonthSalesQty_goa.txt"):
            item_id = ids.get((row.get("ItemId") or "").strip())
            if item_id is None:
                continue
            year = int(float(row["Year"]))
            month = int(float(row["Month"]))
            qty = _num(row.get("SalesQty"))
            if qty is None:
                continue
            sales_rows.append((item_id, date(year, month, 1), qty))
        cursor.executemany(
            f"INSERT INTO `{PHYSICAL_NAME}`.item_histories (item_id, consumption_date, qty) VALUES (%s, %s, %s)",
            sales_rows,
        )
        counts["sales"] = len(sales_rows)
        cursor.executemany(
            f"""
            INSERT INTO `{PHYSICAL_NAME}`.m7_forecast_class
                (code, label, handling, history_for_links, in_export, note)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            CLASS_ROWS,
        )
        class_rows = [(item, code) for item, code in classes.items() if item in ids]
        cursor.executemany(
            f"INSERT INTO `{PHYSICAL_NAME}`.m7_item_class (item_number, class_code) VALUES (%s, %s)",
            class_rows,
        )
        counts["classes"] = len(class_rows)
    conn.commit()
    return counts


def register(conn, email: str, username: str, password: str) -> None:
    assert_m7_database_allowed(LOGICAL_NAME)
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    pages = {
        "visiblePages": ["m7Forecast", "m7Admin"],
        "catalogTable": "items",
    }
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = 'nostradamus_master'
              AND table_name = 'database_connections'
              AND column_name = 'product'
            """
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE database_connections ADD COLUMN product VARCHAR(32) NULL")
        cursor.execute(
            """
            INSERT INTO database_connections
                (name, display_name, host, port, database_name, username, password, is_active, api_url)
            SELECT %s, %s, host, port, %s, username, password, 1, api_url
            FROM database_connections WHERE name = 'consumables'
            """,
            (LOGICAL_NAME, "Goa", PHYSICAL_NAME),
        )
        cursor.execute(
            "UPDATE database_connections SET product = 'm7' WHERE name = %s",
            (LOGICAL_NAME,),
        )
        cursor.execute(
            """
            INSERT INTO users (username, email, password_hash, database_name, is_admin)
            VALUES (%s, %s, %s, %s, 0)
            """,
            (username, email, password_hash, LOGICAL_NAME),
        )
        cursor.execute(
            """
            INSERT INTO db_ui_config (db_name, config_json)
            VALUES (%s, %s)
            """,
            (LOGICAL_NAME, json.dumps(pages)),
        )
    conn.commit()


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: load_goa_test_db.py <goa-folder> <test-user-password>", file=sys.stderr)
        return 2
    folder = pathlib.Path(sys.argv[1])
    test_password = sys.argv[2]
    if len(test_password) < 8:
        print("Test user password must be at least 8 characters", file=sys.stderr)
        return 2
    try:
        assert_m7_database_allowed(LOGICAL_NAME)
        assert_m7_database_allowed(PHYSICAL_NAME)
    except LiveDatabaseRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if SOURCE_SCHEMA.casefold() == PHYSICAL_NAME.casefold():
        print("Refusing to load into the source schema", file=sys.stderr)
        return 2

    master = connect("nostradamus_master")
    try:
        copy_structure(master)
        goa = connect(PHYSICAL_NAME)
        try:
            counts = load(goa, folder)
        finally:
            goa.close()
        register(master, "m7.goa@nostradamus.test", "M7 Goa", test_password)
    finally:
        master.close()
    print(
        f"goa items {counts['items']}, sales months {counts['sales']}, "
        f"classes {counts['classes']}, tables {len(CREATE_TABLES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
