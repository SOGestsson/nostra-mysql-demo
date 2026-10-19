#!/usr/bin/env python3
"""Create M7 forecast tables on one new customer database.

Pass the logical database name. drilling, smart_stock, and consumables are
refused. There is no --all mode. Nothing is altered on existing tables.

Connection settings come from MASTER_DB_HOST, MASTER_DB_PORT, MASTER_DB_USER,
and MASTER_DB_PASSWORD. The password is required and has no default.
"""

from __future__ import annotations

import os
import pathlib
import sys

import mysql.connector

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.m7_schema import LiveDatabaseRefused, assert_m7_database_allowed, create_m7_tables


def _master_config() -> dict:
    password = os.getenv("MASTER_DB_PASSWORD", "").strip()
    if not password:
        raise SystemExit("MASTER_DB_PASSWORD is required")
    return dict(
        host=os.getenv("MASTER_DB_HOST", os.getenv("MYSQL_HOST", "192.168.1.50")),
        port=int(os.getenv("MASTER_DB_PORT", "4406")),
        user=os.getenv("MASTER_DB_USER", "root"),
        password=password,
        database="nostradamus_master",
        connection_timeout=15,
    )


def main() -> int:
    if "--all" in sys.argv:
        print("Refusing --all. Name the new customer database explicitly.", file=sys.stderr)
        return 2
    names = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if len(names) != 1:
        print("Usage: add_m7_forecast_tables.py <new-customer-database>", file=sys.stderr)
        return 2
    try:
        logical_name = assert_m7_database_allowed(names[0])
    except (LiveDatabaseRefused, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    master_config = _master_config()
    master = mysql.connector.connect(**master_config)
    try:
        with master.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT name, database_name FROM database_connections WHERE is_active = 1 AND name = %s",
                (logical_name,),
            )
            row = cursor.fetchone()
        if not row:
            print(f"Unknown logical database: {logical_name}", file=sys.stderr)
            return 2
        physical = row["database_name"]
        if str(physical).casefold() in {"drilling", "smart_stock", "consumables"}:
            print(f"Refusing physical database {physical}", file=sys.stderr)
            return 2
        data_conn = mysql.connector.connect(
            host=master_config["host"],
            port=master_config["port"],
            user=master_config["user"],
            password=master_config["password"],
            database=physical,
            connection_timeout=15,
        )
        try:
            states = create_m7_tables(data_conn)
        finally:
            data_conn.close()
    finally:
        master.close()
    for name, state in states.items():
        print(f"{name}: {state} ({physical}, {logical_name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
