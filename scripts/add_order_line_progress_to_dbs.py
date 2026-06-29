#!/usr/bin/env python3
"""Add order_lines.progress and order_line_progress to db_ui_config."""

from __future__ import annotations

import json
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

ORDER_LINE_PROGRESS_EDITOR = {
    "type": "enum",
    "table": "order_lines",
    "column": "progress",
    "options": ["Not Started", "Work in progress", "Finished"],
}

PROGRESS_STATUS_VALUES = {
    "not started",
    "work in progress",
    "finished",
}


def migrate_progress_schema(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'progress'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN progress VARCHAR(50) NULL DEFAULT NULL"
            )
    conn.commit()


def migrate_misplaced_progress_from_status(conn) -> None:
    """Move workflow progress values accidentally stored in status back to progress."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE order_lines
            SET progress = status,
                status = NULL
            WHERE status IS NOT NULL
              AND LOWER(TRIM(status)) IN ('not started', 'work in progress', 'finished')
            """
        )
    conn.commit()


def patch_ui_config(config: dict) -> dict:
    out = dict(config)
    visible = list(out.get("visibleColumns") or [])
    editable = list(out.get("editableColumns") or [])
    editors = dict(out.get("columnEditors") or {})

    if "order_line_progress" not in visible:
        if "order_line_status" in visible:
            idx = visible.index("order_line_status") + 1
            visible.insert(idx, "order_line_progress")
        elif "order_po" in visible:
            idx = visible.index("order_po") + 1
            visible.insert(idx, "order_line_progress")
        else:
            visible.append("order_line_progress")
    if "order_line_progress" not in editable:
        editable.append("order_line_progress")
    if editors.get("order_line_progress", {}).get("type") != "enum":
        editors["order_line_progress"] = ORDER_LINE_PROGRESS_EDITOR

    out["visibleColumns"] = visible
    out["editableColumns"] = editable
    out["columnEditors"] = editors
    return out


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
            if physical not in migrated:
                data_conn = mysql.connector.connect(
                    host=MASTER["host"],
                    port=MASTER["port"],
                    user=MASTER["user"],
                    password=MASTER["password"],
                    database=physical,
                    connection_timeout=15,
                )
                try:
                    migrate_progress_schema(data_conn)
                    migrate_misplaced_progress_from_status(data_conn)
                    print(f"schema ok: {physical} ({logical_name})")
                finally:
                    data_conn.close()
                migrated.add(physical)

            with master.cursor(dictionary=True) as cursor:
                cursor.execute(
                    "SELECT config_json FROM db_ui_config WHERE db_name = %s",
                    (logical_name,),
                )
                row = cursor.fetchone()
            if not row:
                print(f"no ui config for {logical_name}", file=sys.stderr)
                continue

            config = patch_ui_config(json.loads(row["config_json"]))
            with master.cursor() as cursor:
                cursor.execute(
                    "UPDATE db_ui_config SET config_json = %s WHERE db_name = %s",
                    (json.dumps(config, ensure_ascii=False), logical_name),
                )
            master.commit()
            print(f"config ok: {logical_name} (order_line_progress visible+editable)")
    finally:
        master.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
