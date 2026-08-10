#!/usr/bin/env python3
"""
Create v_sim_optimal_plan (daily, fast), v_sim_optimal_plan_detail (per item), daily table.

Deep Dive constants: 90 USD fixed shipping per delivery day, 18% inventory holding p.a.

Usage (heima / LAN):
  python scripts/create_sim_optimal_plan_view.py --all
  python scripts/create_sim_optimal_plan_view.py consumables Demo

Env: MASTER_DB_HOST (default 192.168.1.50), MASTER_DB_PORT, MASTER_DB_USER, MASTER_DB_PASSWORD
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mysql.connector

from app import db

MASTER = dict(
    host=os.getenv("MASTER_DB_HOST", os.getenv("MYSQL_HOST", "192.168.1.50")),
    port=int(os.getenv("MASTER_DB_PORT", os.getenv("MYSQL_PORT", "4406"))),
    user=os.getenv("MASTER_DB_USER", os.getenv("MYSQL_USER", "root")),
    password=os.getenv("MASTER_DB_PASSWORD", os.getenv("MYSQL_PASSWORD", "Superman")),
    database="nostradamus_master",
    connection_timeout=15,
)


def main() -> int:
    migrate_all = "--all" in sys.argv
    db_names = [a for a in sys.argv[1:] if not a.startswith("-")]

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
                db.ensure_sim_optimal_plan_view(data_conn)
                print(
                    f"views {db.SIM_OPTIMAL_PLAN_VIEW}, {db.SIM_OPTIMAL_PLAN_DETAIL_VIEW}, "
                    f"{db.SIM_OPTIMAL_PLAN_DAILY_VIEW} + table {db.SIM_OPTIMAL_PLAN_DAILY_TABLE} "
                    f"ok: {physical} ({logical_name})"
                )
            finally:
                data_conn.close()
            migrated.add(physical)
    finally:
        master.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
