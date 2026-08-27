#!/usr/bin/env python3
"""Create stock_history and roi_result in tenant databases.

Additive only: creates new tables and never alters existing ones. Both tables
also auto-create on first write, so this script is for pre-provisioning.
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

CREATE_STOCK_HISTORY = """
    CREATE TABLE IF NOT EXISTS stock_history (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        item_id INT NOT NULL,
        stock_date DATE NOT NULL,
        stock_qty DECIMAL(18, 4) NOT NULL,
        UNIQUE KEY uq_stock_history_item_date (item_id, stock_date),
        KEY idx_stock_history_item (item_id)
    )
"""

CREATE_ROI_RESULT = """
    CREATE TABLE IF NOT EXISTS roi_result (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        item_id INT NOT NULL,
        method VARCHAR(32) NOT NULL DEFAULT 'point_estimate',
        run_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        model_used VARCHAR(64) NULL,
        forecast_freq VARCHAR(8) NULL,
        service_level DECIMAL(8, 4) NULL,
        ss_source VARCHAR(16) NULL,
        ss_override DECIMAL(18, 4) NULL,
        unit_cost DECIMAL(18, 4) NULL,
        del_time DECIMAL(18, 4) NULL,
        buy_freq DECIMAL(18, 4) NULL,
        moq DECIMAL(18, 4) NULL,
        order_multiple DECIMAL(18, 4) NULL,
        cover_days DECIMAL(18, 4) NULL,
        order_period_days DECIMAL(18, 4) NULL,
        forecast_lead_qty DECIMAL(18, 4) NULL,
        forecast_order_qty DECIMAL(18, 4) NULL,
        order_qty DECIMAL(18, 4) NULL,
        cycle_stock DECIMAL(18, 4) NULL,
        safety_stock_forecast DECIMAL(18, 4) NULL,
        safety_stock_used DECIMAL(18, 4) NULL,
        expected_stock DECIMAL(18, 4) NULL,
        expected_value DECIMAL(18, 4) NULL,
        current_stock DECIMAL(18, 4) NULL,
        current_value DECIMAL(18, 4) NULL,
        avg_stock_3m DECIMAL(18, 4) NULL,
        avg_stock_6m DECIMAL(18, 4) NULL,
        avg_stock_12m DECIMAL(18, 4) NULL,
        avg_value_3m DECIMAL(18, 4) NULL,
        avg_value_6m DECIMAL(18, 4) NULL,
        avg_value_12m DECIMAL(18, 4) NULL,
        delta_qty_vs_current DECIMAL(18, 4) NULL,
        delta_value_vs_current DECIMAL(18, 4) NULL,
        UNIQUE KEY uq_roi_item (item_id),
        KEY idx_roi_item (item_id)
    )
"""


def _table_exists(cursor, name: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (name,))
    return cursor.fetchone() is not None


def create_roi_tables(conn) -> dict[str, str]:
    states: dict[str, str] = {}
    with conn.cursor() as cursor:
        states["stock_history"] = "already present" if _table_exists(cursor, "stock_history") else "created"
        cursor.execute(CREATE_STOCK_HISTORY)
        states["roi_result"] = "already present" if _table_exists(cursor, "roi_result") else "created"
        cursor.execute(CREATE_ROI_RESULT)
    conn.commit()
    return states


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
                states = create_roi_tables(data_conn)
                print(
                    f"stock_history {states['stock_history']}, "
                    f"roi_result {states['roi_result']}: {physical} ({logical_name})"
                )
            finally:
                data_conn.close()
            migrated.add(physical)
    finally:
        master.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
