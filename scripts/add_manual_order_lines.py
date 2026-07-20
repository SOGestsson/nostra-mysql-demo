#!/usr/bin/env python3
"""Add manual order line columns to order_lines on all active tenant databases."""

from __future__ import annotations

import os
import sys

import mysql.connector

MASTER = dict(
    host=os.getenv("MASTER_DB_HOST", os.getenv("MYSQL_HOST", "192.168.1.50")),
    port=int(os.getenv("MASTER_DB_PORT", "4406")),
    user=os.getenv("MASTER_DB_USER", "root"),
    password=os.getenv("MASTER_DB_PASSWORD", "Superman"),
    database="nostradamus_master",
    connection_timeout=15,
)


def migrate_manual_order_lines(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'is_manual'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN is_manual TINYINT NOT NULL DEFAULT 0"
            )
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'manual_item_number'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN manual_item_number VARCHAR(100) NULL DEFAULT NULL"
            )
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'manual_description'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN manual_description VARCHAR(255) NULL DEFAULT NULL"
            )
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'manual_vendor_name'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN manual_vendor_name VARCHAR(255) NULL DEFAULT NULL"
            )
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'manual_unit_price'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN manual_unit_price DECIMAL(12,2) NULL DEFAULT NULL"
            )
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'item_id'")
        item_id_col = cursor.fetchone()
        if item_id_col and str(item_id_col[2]).upper() == "NO":
            cursor.execute("ALTER TABLE order_lines MODIFY item_id INT NULL")
    conn.commit()


def main() -> int:
    migrate_all = "--all" in sys.argv
    db_names = [a for a in sys.argv[1:] if a != "--all"]
    master = mysql.connector.connect(**MASTER)
    try:
        with master.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT name, database_name FROM database_connections WHERE is_active = 1 ORDER BY name"
            )
            rows = cursor.fetchall()
            logical_to_physical = {row["name"]: row["database_name"] for row in rows}

        if migrate_all or not db_names:
            targets = [(row["name"], row["database_name"]) for row in rows]
        else:
            targets = []
            for logical_name in db_names:
                physical = logical_to_physical.get(logical_name)
                if not physical:
                    print(f"skip unknown logical db: {logical_name}", file=sys.stderr)
                    continue
                targets.append((logical_name, physical))

        migrated: set[str] = set()
        for logical_name, physical in targets:
            if physical in migrated:
                continue
            data_conn = mysql.connector.connect(
                host=MASTER["host"],
                port=MASTER["port"],
                user=MASTER["user"],
                password=MASTER["password"],
                database=physical,
                connection_timeout=15,
            )
            try:
                migrate_manual_order_lines(data_conn)
                print(f"schema ok: {physical} ({logical_name})")
            finally:
                data_conn.close()
            migrated.add(physical)
    finally:
        master.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
