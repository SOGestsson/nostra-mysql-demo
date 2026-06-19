#!/usr/bin/env python3
"""Add order_po to db_ui_config and migrate order_lines.po schema."""

from __future__ import annotations

import json
import os
import sys

import mysql.connector

MASTER = dict(
    host=os.getenv("MASTER_DB_HOST", "127.0.0.1"),
    port=int(os.getenv("MASTER_DB_PORT", "4406")),
    user=os.getenv("MASTER_DB_USER", "root"),
    password=os.getenv("MASTER_DB_PASSWORD", "Superman"),
    database="nostradamus_master",
)

ORDER_PO_EDITOR = {
    "type": "native",
    "table": "order_lines",
    "column": "po",
}


def migrate_po_schema(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'po'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE order_lines ADD COLUMN po VARCHAR(200) NULL DEFAULT NULL")
        cursor.execute("SHOW COLUMNS FROM items LIKE 'po'")
        if cursor.fetchone():
            cursor.execute(
                """
                UPDATE order_lines ol
                INNER JOIN items i ON ol.item_id = i.id
                SET ol.po = i.po
                WHERE i.po IS NOT NULL AND i.po != ''
                  AND (ol.po IS NULL OR ol.po = '')
                """
            )
            cursor.execute("ALTER TABLE items DROP COLUMN po")
    conn.commit()


def patch_ui_config(config: dict) -> dict:
    out = dict(config)
    visible = list(out.get("visibleColumns") or [])
    editable = list(out.get("editableColumns") or [])
    editors = dict(out.get("columnEditors") or {})

    if "po" in visible:
        visible = [("order_po" if k == "po" else k) for k in visible]
    if "po" in editable:
        editable = [("order_po" if k == "po" else k) for k in editable]
    if "po" in editors and "order_po" not in editors:
        editors["order_po"] = editors.pop("po")

    if "order_po" not in visible:
        if "order_qty_override" in visible:
            idx = visible.index("order_qty_override") + 1
            visible.insert(idx, "order_po")
        else:
            visible.append("order_po")
    if "order_po" not in editable:
        editable.append("order_po")
    editors["order_po"] = ORDER_PO_EDITOR

    out["visibleColumns"] = visible
    out["editableColumns"] = editable
    out["columnEditors"] = editors
    return out


def main() -> int:
    db_names = sys.argv[1:] or ["drilling", "demo"]
    master = mysql.connector.connect(**MASTER)
    try:
        with master.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT name, database_name FROM database_connections WHERE is_active = 1"
            )
            logical_to_physical = {row["name"]: row["database_name"] for row in cursor.fetchall()}

        migrated: set[str] = set()
        for logical_name in db_names:
            physical = logical_to_physical.get(logical_name)
            if not physical:
                print(f"skip unknown logical db: {logical_name}", file=sys.stderr)
                continue

            if physical not in migrated:
                data_conn = mysql.connector.connect(
                    host=MASTER["host"],
                    port=MASTER["port"],
                    user=MASTER["user"],
                    password=MASTER["password"],
                    database=physical,
                )
                try:
                    migrate_po_schema(data_conn)
                    print(f"schema ok: {physical}")
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
            print(f"config ok: {logical_name} (order_po visible+editable)")
    finally:
        master.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
