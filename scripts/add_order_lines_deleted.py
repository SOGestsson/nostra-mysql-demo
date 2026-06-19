#!/usr/bin/env python3
"""Add order_lines.deleted column for soft-delete on order lines."""

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


def migrate_deleted_schema(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'deleted'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN deleted TINYINT NOT NULL DEFAULT 0"
            )
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
                print(f"already migrated: {physical} ({logical_name})")
                continue
            data_conn = mysql.connector.connect(
                host=MASTER["host"],
                port=MASTER["port"],
                user=MASTER["user"],
                password=MASTER["password"],
                database=physical,
            )
            try:
                migrate_deleted_schema(data_conn)
                print(f"schema ok: {physical} ({logical_name})")
            finally:
                data_conn.close()
            migrated.add(physical)
    finally:
        master.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
