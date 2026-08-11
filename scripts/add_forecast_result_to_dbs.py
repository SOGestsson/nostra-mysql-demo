#!/usr/bin/env python3
"""Create the forecast_result table in tenant databases.

Additive only: creates one new table and never alters existing ones. The table
also auto-creates on first forecast write, so this script is for pre-provisioning.
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

CREATE_FORECAST_RESULT = """
    CREATE TABLE IF NOT EXISTS forecast_result (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        item_id INT NOT NULL,
        forecast_date DATE NOT NULL,
        forecast DECIMAL(18, 4) NULL,
        upper_70 DECIMAL(18, 4) NULL,
        upper_90 DECIMAL(18, 4) NULL,
        upper_95 DECIMAL(18, 4) NULL,
        model_used VARCHAR(64) NULL,
        freq VARCHAR(8) NOT NULL DEFAULT 'D',
        run_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_forecast_item_date (item_id, forecast_date),
        KEY idx_forecast_item (item_id)
    )
"""


def create_forecast_result(conn) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'forecast_result'")
        already_present = cursor.fetchone() is not None
        cursor.execute(CREATE_FORECAST_RESULT)
    conn.commit()
    return already_present


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
                already_present = create_forecast_result(data_conn)
                state = "already present" if already_present else "created"
                print(f"forecast_result {state}: {physical} ({logical_name})")
            finally:
                data_conn.close()
            migrated.add(physical)
    finally:
        master.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
