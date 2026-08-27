#!/usr/bin/env python3
"""Add moq, order_multiple, safety_stock to items on smart_stock only.

Other tenants are left unchanged; db-api reads these columns only when present.
"""

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

DEFAULT_LOGICAL_DB = "smart_stock"
COLUMNS = ("moq", "order_multiple", "safety_stock")


def add_item_columns(conn) -> dict[str, str]:
    states: dict[str, str] = {}
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM items")
        existing = {row[0] for row in cursor.fetchall()}
        for name in COLUMNS:
            if name in existing:
                states[name] = "already present"
                continue
            cursor.execute(
                f"ALTER TABLE items ADD COLUMN `{name}` DECIMAL(18, 4) NULL"
            )
            states[name] = "created"
    conn.commit()
    return states


def main() -> int:
    requested = [a for a in sys.argv[1:] if not a.startswith("-")]
    logical_names = requested or [DEFAULT_LOGICAL_DB]
    master = mysql.connector.connect(**MASTER)
    try:
        with master.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT name, database_name FROM database_connections WHERE is_active = 1"
            )
            logical_to_physical = {row["name"]: row["database_name"] for row in cursor.fetchall()}

        for logical_name in logical_names:
            physical = logical_to_physical.get(logical_name)
            if not physical:
                # Also accept a physical schema name.
                physical = logical_name
            data_conn = mysql.connector.connect(
                host=MASTER["host"],
                port=MASTER["port"],
                user=MASTER["user"],
                password=MASTER["password"],
                database=physical,
                connection_timeout=15,
            )
            try:
                states = add_item_columns(data_conn)
                detail = ", ".join(f"{k} {v}" for k, v in states.items())
                print(f"items columns {physical} ({logical_name}): {detail}")
            finally:
                data_conn.close()
    finally:
        master.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
