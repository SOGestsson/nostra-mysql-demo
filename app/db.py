from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterator

import mysql.connector
from mysql.connector import MySQLConnection

DEFAULT_VENDOR_OVERRIDE_DAYS = 30


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    is_nullable: bool
    column_key: str
    extra: str


def _get_db_config(name: str) -> dict:
    master = mysql.connector.connect(
        host=os.getenv("MASTER_DB_HOST", "raspberrypi.local"),
        port=int(os.getenv("MASTER_DB_PORT", "4406")),
        user=os.getenv("MASTER_DB_USER", "root"),
        password=os.getenv("MASTER_DB_PASSWORD", "Superman"),
        database="nostradamus_master",
    )
    try:
        with master.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT host, port, database_name, username, password "
                "FROM database_connections WHERE name = %s AND is_active = 1 LIMIT 1",
                (name,),
            )
            row = cursor.fetchone()
    finally:
        master.close()
    if not row:
        raise ValueError(f"Unknown database: '{name}'")
    return row


def _get_vendor_override_days(name: str | None) -> int:
    if not name:
        return DEFAULT_VENDOR_OVERRIDE_DAYS

    master = mysql.connector.connect(
        host=os.getenv("MASTER_DB_HOST", "raspberrypi.local"),
        port=int(os.getenv("MASTER_DB_PORT", "4406")),
        user=os.getenv("MASTER_DB_USER", "root"),
        password=os.getenv("MASTER_DB_PASSWORD", "Superman"),
        database="nostradamus_master",
    )
    try:
        with master.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT config_json FROM db_ui_config WHERE db_name = %s", (name,))
            row = cursor.fetchone()
    finally:
        master.close()

    if not row:
        return DEFAULT_VENDOR_OVERRIDE_DAYS

    try:
        days = int(json.loads(row["config_json"]).get("vendorOverrideDays", DEFAULT_VENDOR_OVERRIDE_DAYS))
    except (TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_VENDOR_OVERRIDE_DAYS

    return max(0, min(days, 3650))


def list_active_databases() -> list[dict[str, str]]:
    master = mysql.connector.connect(
        host=os.getenv("MASTER_DB_HOST", "raspberrypi.local"),
        port=int(os.getenv("MASTER_DB_PORT", "4406")),
        user=os.getenv("MASTER_DB_USER", "root"),
        password=os.getenv("MASTER_DB_PASSWORD", "Superman"),
        database="nostradamus_master",
    )
    try:
        with master.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT name, display_name FROM database_connections WHERE is_active = 1 ORDER BY display_name"
            )
            rows = cursor.fetchall()
    finally:
        master.close()
    return [{"name": row["name"], "display_name": row["display_name"] or row["name"]} for row in rows]


def get_connection(database: str | None = None) -> MySQLConnection:
    if database:
        cfg = _get_db_config(database)
        return mysql.connector.connect(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["username"],
            password=cfg["password"],
            database=cfg["database_name"],
            autocommit=False,
        )
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "raspberrypi.local"),
        port=int(os.getenv("MYSQL_PORT", "4406")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "Superman"),
        database=os.getenv("MYSQL_DATABASE", "smart_stock"),
        autocommit=False,
    )


@contextmanager
def connection(database: str | None = None) -> Iterator[MySQLConnection]:
    conn = get_connection(database)
    try:
        yield conn
    finally:
        conn.close()


def list_tables(database: str | None = None) -> list[str]:
    with connection(database) as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        return [str(row[0]) for row in cursor.fetchall()]


def get_columns(conn: MySQLConnection, table_name: str) -> list[ColumnInfo]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                column_name,
                data_type,
                is_nullable,
                column_key,
                extra
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        rows = cursor.fetchall()

    return [
        ColumnInfo(
            name=str(row[0]),
            data_type=str(row[1]).lower(),
            is_nullable=str(row[2]).upper() == "YES",
            column_key=str(row[3]),
            extra=str(row[4]),
        )
        for row in rows
    ]


def ensure_table_exists(conn: MySQLConnection, table_name: str) -> list[ColumnInfo]:
    columns = get_columns(conn, table_name)
    if not columns:
        raise ValueError(f"Table not found: {table_name}")
    return columns


def get_primary_key_columns(columns: list[ColumnInfo]) -> list[ColumnInfo]:
    return [column for column in columns if column.column_key == "PRI"]


def require_single_primary_key(columns: list[ColumnInfo], table_name: str) -> ColumnInfo:
    primary_key_columns = get_primary_key_columns(columns)
    if len(primary_key_columns) != 1:
        raise ValueError(
            f"Table '{table_name}' must have exactly one primary key column for this endpoint"
        )
    return primary_key_columns[0]


def _ensure_vendor_overrides_table(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vendor_overrides (
                item_id INT NOT NULL PRIMARY KEY,
                vendor_name VARCHAR(255) NOT NULL,
                set_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
    conn.commit()


def set_vendor_override(item_id: int, vendor_name: str, database: str | None = None) -> None:
    with connection(database) as conn:
        _ensure_vendor_overrides_table(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO vendor_overrides (item_id, vendor_name, set_at)
                VALUES (%s, %s, NOW())
                ON DUPLICATE KEY UPDATE vendor_name = VALUES(vendor_name), set_at = NOW()
                """,
                (item_id, vendor_name),
            )
        conn.commit()


def _ensure_purchasing_method_overrides_table(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchasing_method_overrides (
                item_id INT NOT NULL PRIMARY KEY,
                purchasing_method VARCHAR(64) NOT NULL,
                set_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
    conn.commit()


def _apply_purchasing_method_override(
    conn: MySQLConnection,
    item_id: int,
    purchasing_method: Any,
) -> None:
    _ensure_purchasing_method_overrides_table(conn)
    name = str(purchasing_method or "").strip()
    with conn.cursor() as cursor:
        if not name:
            cursor.execute(
                "DELETE FROM purchasing_method_overrides WHERE item_id = %s",
                (item_id,),
            )
        else:
            cursor.execute(
                """
                INSERT INTO purchasing_method_overrides (item_id, purchasing_method, set_at)
                VALUES (%s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    purchasing_method = VALUES(purchasing_method),
                    set_at = NOW()
                """,
                (item_id, name),
            )


def set_purchasing_method_override(
    item_id: int,
    purchasing_method: str,
    database: str | None = None,
) -> None:
    with connection(database) as conn:
        _apply_purchasing_method_override(conn, item_id, purchasing_method)
        conn.commit()


def _item_override_join_sql(vendor_override_days: int) -> str:
    return (
        "LEFT JOIN vendor_overrides vo "
        f"ON items.id = vo.item_id AND vo.set_at > DATE_SUB(NOW(), INTERVAL {vendor_override_days} DAY) "
        "LEFT JOIN purchasing_method_overrides pmo "
        "ON items.id = pmo.item_id"
    )


def _item_override_select_fields() -> str:
    return (
        "items.*, "
        "items.vendor_name AS item_vendor_name, "
        "COALESCE(vo.vendor_name, items.vendor_name) AS vendor_name, "
        "vo.set_at AS vendor_override_set_at, "
        "items.purchasing_method AS item_purchasing_method, "
        "COALESCE(pmo.purchasing_method, items.purchasing_method) AS purchasing_method, "
        "pmo.set_at AS purchasing_method_override_set_at"
    )


def get_item_with_overrides(item_id: int, database: str | None = None) -> dict[str, Any] | None:
    with connection(database) as conn:
        row = _fetch_item_with_overrides_conn(conn, item_id, database)
        return normalize_row(row) if row else None


def _fetch_item_with_overrides_conn(
    conn: MySQLConnection,
    item_id: int,
    database: str | None = None,
) -> dict[str, Any] | None:
    _ensure_vendor_overrides_table(conn)
    _ensure_purchasing_method_overrides_table(conn)
    vendor_override_days = _get_vendor_override_days(database)
    query = (
        f"SELECT {_item_override_select_fields()} "
        f"FROM items {_item_override_join_sql(vendor_override_days)} "
        "WHERE items.id = %s"
    )
    return _get_single_row(conn, query, (item_id,))


def _migrate_po_to_order_lines(conn: MySQLConnection) -> None:
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


def _migrate_order_lines_deleted_column(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'deleted'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN deleted TINYINT NOT NULL DEFAULT 0"
            )
    conn.commit()


def _migrate_order_lines_status_column(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'status'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN status VARCHAR(50) NULL DEFAULT NULL"
            )
    conn.commit()


def _migrate_order_lines_progress_column(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'progress'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN progress VARCHAR(50) NULL DEFAULT NULL"
            )
    conn.commit()


def _migrate_order_lines_assigned_to_column(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'assigned_to'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN assigned_to INT NULL DEFAULT NULL"
            )
    conn.commit()


def _migrate_order_lines_purch_sugg_confirmed_column(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM order_lines LIKE 'purch_sugg_confirmed'")
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE order_lines ADD COLUMN purch_sugg_confirmed TINYINT NOT NULL DEFAULT 0"
            )
    conn.commit()


def _migrate_order_lines_manual_columns(conn: MySQLConnection) -> None:
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


def _ensure_vendor_info_manual_column(conn: MySQLConnection) -> None:
    try:
        columns = {column.name for column in get_columns(conn, "vendor_info")}
    except ValueError:
        return
    if "is_manual" in columns:
        return
    with conn.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE vendor_info ADD COLUMN is_manual TINYINT NOT NULL DEFAULT 0"
        )
    conn.commit()


def list_vendors(database: str | None = None) -> list[dict[str, Any]]:
    with connection(database) as conn:
        _ensure_vendor_info_manual_column(conn)
        ensure_table_exists(conn, "vendor_info")
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT vendor_name, COALESCE(is_manual, 0) AS is_manual
                FROM vendor_info
                WHERE vendor_name IS NOT NULL AND vendor_name != ''
                ORDER BY vendor_name
                """
            )
            rows = cursor.fetchall()
    return [normalize_row(row) for row in rows]


def add_manual_vendor(vendor_name: str, database: str | None = None) -> dict[str, Any]:
    name = (vendor_name or "").strip()
    if not name:
        raise ValueError("vendor_name is required")

    with connection(database) as conn:
        _ensure_vendor_info_manual_column(conn)
        columns = {column.name for column in ensure_table_exists(conn, "vendor_info")}
        if "vendor_name" not in columns:
            raise ValueError("vendor_info table must include vendor_name column")

        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT vendor_name, COALESCE(is_manual, 0) AS is_manual "
                "FROM vendor_info WHERE vendor_name = %s LIMIT 1",
                (name,),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    "UPDATE vendor_info SET is_manual = 1 WHERE vendor_name = %s",
                    (name,),
                )
                if "vendor_code" in columns:
                    cursor.execute(
                        "UPDATE vendor_info SET vendor_code = %s "
                        "WHERE vendor_name = %s AND (vendor_code IS NULL OR TRIM(vendor_code) = '')",
                        (name, name),
                    )
            else:
                insert_cols = ["vendor_name", "is_manual"]
                insert_vals: list[Any] = [name, 1]
                if "vendor_code" in columns:
                    insert_cols.append("vendor_code")
                    insert_vals.append(name)
                placeholders = ", ".join(["%s"] * len(insert_cols))
                col_sql = ", ".join(quote_ident(c) for c in insert_cols)
                cursor.execute(
                    f"INSERT INTO vendor_info ({col_sql}) VALUES ({placeholders})",
                    tuple(insert_vals),
                )
        conn.commit()

        row = _get_single_row(
            conn,
            "SELECT vendor_name, COALESCE(is_manual, 0) AS is_manual "
            "FROM vendor_info WHERE vendor_name = %s",
            (name,),
        )
        if not row:
            raise ValueError("Failed to save manual vendor")
        return normalize_row(row)


def replace_synced_vendors(vendor_names: list[str], database: str | None = None) -> dict[str, int]:
    """Replace auto-synced vendors while keeping manual entries."""
    cleaned = []
    seen: set[str] = set()
    for raw in vendor_names:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        cleaned.append(name)

    with connection(database) as conn:
        _ensure_vendor_info_manual_column(conn)
        ensure_table_exists(conn, "vendor_info")
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM vendor_info WHERE COALESCE(is_manual, 0) = 0")
            deleted = cursor.rowcount
            inserted = 0
            for name in cleaned:
                cursor.execute(
                    """
                    INSERT INTO vendor_info (vendor_name, is_manual)
                    VALUES (%s, 0)
                    ON DUPLICATE KEY UPDATE
                        is_manual = IF(COALESCE(is_manual, 0) = 1, 1, 0)
                    """,
                    (name,),
                )
                if cursor.rowcount > 0:
                    inserted += 1
        conn.commit()
        return {"deleted": deleted, "inserted_or_updated": inserted, "received": len(cleaned)}


def list_rows(table_name: str, limit: int, offset: int, database: str | None = None, stock_out: bool = False) -> list[dict[str, Any]]:
    with connection(database) as conn:
        ensure_table_exists(conn, table_name)
        if table_name == 'items':
            _ensure_vendor_overrides_table(conn)
            _ensure_purchasing_method_overrides_table(conn)
            vendor_override_days = _get_vendor_override_days(database)
            join = _item_override_join_sql(vendor_override_days)
            select = f"SELECT {_item_override_select_fields()} FROM items {join}"
            if stock_out:
                query = f"{select} WHERE items.stock_level <= 0 OR items.stock_level IS NULL"
                params: tuple = ()
            else:
                query = f"{select} LIMIT %s OFFSET %s"
                params = (limit, offset)
        else:
            if stock_out:
                query = f"SELECT * FROM {quote_ident(table_name)} WHERE stock_level <= 0 OR stock_level IS NULL"
                params = ()
            else:
                query = f"SELECT * FROM {quote_ident(table_name)} LIMIT %s OFFSET %s"
                params = (limit, offset)
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        return [normalize_row(row) for row in rows]


def get_row(table_name: str, row_id: Any, database: str | None = None) -> dict[str, Any] | None:
    if table_name == "items":
        return get_item_with_overrides(row_id, database)
    with connection(database) as conn:
        columns = ensure_table_exists(conn, table_name)
        pk_column = require_single_primary_key(columns, table_name)
        query = (
            f"SELECT * FROM {quote_ident(table_name)} "
            f"WHERE {quote_ident(pk_column.name)} = %s"
        )
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query, (row_id,))
            row = cursor.fetchone()
        return normalize_row(row) if row else None


def create_row(table_name: str, payload: dict[str, Any], database: str | None = None) -> dict[str, Any]:
    if not payload:
        raise ValueError("Payload must include at least one field")

    with connection(database) as conn:
        columns = ensure_table_exists(conn, table_name)
        valid_columns = {column.name: column for column in columns}
        insertable = {
            column.name
            for column in columns
            if "auto_increment" not in column.extra.lower()
        }
        data = filter_payload(payload, valid_columns.keys())
        if not data:
            raise ValueError("Payload does not contain valid columns")

        for key in data:
            if key not in insertable:
                raise ValueError(f"Column '{key}' cannot be set explicitly")

        column_names = list(data.keys())
        query = (
            f"INSERT INTO {quote_ident(table_name)} "
            f"({', '.join(quote_ident(name) for name in column_names)}) "
            f"VALUES ({', '.join(['%s'] * len(column_names))})"
        )

        with conn.cursor() as cursor:
            cursor.execute(query, tuple(data[name] for name in column_names))
            conn.commit()

            pk_column = get_primary_key_columns(columns)
            if len(pk_column) == 1 and "auto_increment" in pk_column[0].extra.lower():
                row_id = cursor.lastrowid
                result = get_row(table_name, row_id, database)
                if result:
                    return result

        if len(get_primary_key_columns(columns)) == 1:
            pk_name = get_primary_key_columns(columns)[0].name
            if pk_name in data:
                result = get_row(table_name, data[pk_name], database)
                if result:
                    return result

        return data


def update_row(table_name: str, row_id: Any, payload: dict[str, Any], database: str | None = None) -> dict[str, Any] | None:
    if not payload:
        raise ValueError("Payload must include at least one field")

    with connection(database) as conn:
        columns = ensure_table_exists(conn, table_name)
        pk_column = require_single_primary_key(columns, table_name)
        data = filter_payload(payload, [column.name for column in columns if column.name != pk_column.name])
        if not data:
            raise ValueError("Payload does not contain valid updatable columns")

        if table_name == "items" and "purchasing_method" in data:
            _apply_purchasing_method_override(conn, row_id, data.pop("purchasing_method"))

        if not data:
            conn.commit()
            return get_item_with_overrides(row_id, database)

        assignments = ", ".join(f"{quote_ident(name)} = %s" for name in data)
        query = (
            f"UPDATE {quote_ident(table_name)} "
            f"SET {assignments} "
            f"WHERE {quote_ident(pk_column.name)} = %s"
        )

        with conn.cursor() as cursor:
            cursor.execute(query, tuple(data.values()) + (row_id,))
            conn.commit()

        return get_row(table_name, row_id, database)


def delete_row(table_name: str, row_id: Any, database: str | None = None) -> bool:
    with connection(database) as conn:
        columns = ensure_table_exists(conn, table_name)
        if table_name == "vendor_info":
            _ensure_vendor_info_manual_column(conn)
            existing = get_row(table_name, row_id, database)
            if existing and int(existing.get("is_manual") or 0) == 1:
                raise ValueError("Manual vendors cannot be deleted")
        pk_column = require_single_primary_key(columns, table_name)
        query = (
            f"DELETE FROM {quote_ident(table_name)} "
            f"WHERE {quote_ident(pk_column.name)} = %s"
        )
        with conn.cursor() as cursor:
            cursor.execute(query, (row_id,))
            conn.commit()
            return cursor.rowcount > 0


def get_sim_input_data(
    item_id: int,
    number_of_days: int = 900,
    number_of_simulations: int = 1000,
    service_level: float = 0.95,
    start_day: date | None = None,
    end_day: date | None = None,
    database: str | None = None,
) -> dict[str, Any]:
    if number_of_days < 1:
        raise ValueError("number_of_days must be at least 1")
    if number_of_simulations < 1:
        raise ValueError("number_of_simulations must be at least 1")
    if not 0 < service_level <= 1:
        raise ValueError("service_level must be greater than 0 and at most 1")

    with connection(database) as conn:
        item = _fetch_item_with_overrides_conn(conn, item_id, database)
        if not item:
            raise ValueError(f"Item not found: {item_id}")

        item_number = item.get("item_number") or ""
        hist_cols = {c.name for c in get_columns(conn, "item_histories")}
        if "item_id" in hist_cols:
            history_rows = _get_rows(
                conn,
                """
                SELECT `consumption_date`, SUM(ABS(`qty`)) AS `actual_sale`
                FROM `item_histories`
                WHERE `item_id` = %s
                GROUP BY `consumption_date`
                ORDER BY `consumption_date`
                """,
                (item_id,),
            )
        else:
            history_rows = _get_rows(
                conn,
                """
                SELECT `consumption_date`, SUM(ABS(`qty`)) AS `actual_sale`
                FROM `item_histories`
                WHERE `item_number` = %s
                GROUP BY `consumption_date`
                ORDER BY `consumption_date`
                """,
                (item_number,),
            )

        history_dates = [row["consumption_date"] for row in history_rows if row["consumption_date"]]
        series_end = end_day or date.today()
        default_start = series_end - timedelta(days=number_of_days - 1)
        series_start = start_day or default_start

        if series_end < series_start:
            raise ValueError("end_day must be on or after start_day")

        history_by_day = {
            row["consumption_date"]: int(float(row["actual_sale"] or 0))
            for row in history_rows
        }

        sim_input_his: list[dict[str, Any]] = []
        current_day = series_start
        while current_day <= series_end:
            sim_input_his.append(
                {
                    "item_id": item_id,
                    "actual_sale": history_by_day.get(current_day, 0),
                    "day": current_day.isoformat(),
                }
            )
            current_day += timedelta(days=1)

        order_cols = {c.name for c in get_columns(conn, "on_order")}
        if "item_id" in order_cols:
            on_order_rows = _get_rows(
                conn,
                """
                SELECT `item_id`, `item_number`, `warehouse_name`, `est_deliv_date`, `est_deliv_qty`
                FROM `on_order`
                WHERE `item_id` = %s
                ORDER BY `est_deliv_date`, `id`
                """,
                (item_id,),
            )
            sim_rio_on_order = [
                {
                    "item_id": row.get("item_id"),
                    "item_number": row.get("item_number") or "",
                    "warehouse_name": row.get("warehouse_name") or "",
                    "est_deliv_date": (
                        row.get("est_deliv_date").isoformat()
                        if row.get("est_deliv_date")
                        else None
                    ),
                    "est_deliv_qty": _to_number(row.get("est_deliv_qty"), default=0),
                }
                for row in on_order_rows
            ]
        else:
            on_order_rows = _get_rows(
                conn,
                """
                SELECT `pn`, `est_deliv_date`, `est_deliv_qty`
                FROM `on_order`
                WHERE `pn` = %s
                ORDER BY `est_deliv_date`, `id`
                """,
                (item_number,),
            )
            sim_rio_on_order = [
                {
                    "item_id": item_id,
                    "item_number": row.get("pn") or "",
                    "warehouse_name": "",
                    "est_deliv_date": (
                        row.get("est_deliv_date").isoformat()
                        if row.get("est_deliv_date")
                        else None
                    ),
                    "est_deliv_qty": _to_number(row.get("est_deliv_qty"), default=0),
                }
                for row in on_order_rows
            ]

        sim_rio_items = [
            {
                "actual_stock": _to_number(item.get("stock_level"), default=0),
                "buy_freq": _to_number(item.get("buy_freq"), default=0),
                "del_time": _to_number(item.get("del_time"), default=0),
                "description": item.get("description") or item.get("item_number"),
                "ideal_stock": _to_number(item.get("purchase_suggestion"), default=0),
                "max": _to_number(item.get("max"), default=0),
                "min": _to_number(item.get("min"), default=0),
                "pn": item.get("item_number"),
                "purchasing_method": item.get("purchasing_method") or "",
                "station": item.get("location_name") or item.get("location") or "",
            }
        ]

        sim_rio_item_details = [
            {
                "id": item.get("item_number") or str(item_id),
                "vendor_name": item.get("vendor_name") or "",
            }
        ]

        return {
            "sim_input_his": sim_input_his,
            "sim_rio_items": sim_rio_items,
            "sim_rio_item_details": sim_rio_item_details,
            "sim_rio_on_order": sim_rio_on_order,
            "number_of_days": number_of_days,
            "number_of_simulations": number_of_simulations,
            "service_level": service_level,
        }


def get_forecast_input_data(
    item_id: int,
    forecast_periods: int = 30,
    mode: str = "local",
    local_model: str = "auto_arima",
    season_length: int = 7,
    freq: str = "D",
    start_day: date | None = None,
    end_day: date | None = None,
    database: str | None = None,
) -> dict[str, Any]:
    if forecast_periods < 1:
        raise ValueError("forecast_periods must be at least 1")
    if season_length < 1:
        raise ValueError("season_length must be at least 1")

    with connection(database) as conn:
        hist_cols = {c.name for c in get_columns(conn, "item_histories")}
        if "item_id" in hist_cols:
            history_rows = _get_rows(
                conn,
                """
                SELECT `consumption_date`, SUM(ABS(`qty`)) AS `actual_sale`
                FROM `item_histories`
                WHERE `item_id` = %s
                GROUP BY `consumption_date`
                ORDER BY `consumption_date`
                """,
                (item_id,),
            )
        else:
            item = _get_single_row(conn, "SELECT `item_number` FROM `items` WHERE `id` = %s", (item_id,))
            if not item:
                raise ValueError(f"Item not found: {item_id}")
            history_rows = _get_rows(
                conn,
                """
                SELECT `consumption_date`, SUM(ABS(`qty`)) AS `actual_sale`
                FROM `item_histories`
                WHERE `item_number` = %s
                GROUP BY `consumption_date`
                ORDER BY `consumption_date`
                """,
                (item.get("item_number"),),
            )

        if not history_rows:
            raise ValueError(f"Item not found in item_histories: {item_id}")

        history_dates = [row["consumption_date"] for row in history_rows if row["consumption_date"]]
        first_history_day = min(history_dates) if history_dates else None
        last_history_day = max(history_dates) if history_dates else None

        series_start = start_day or first_history_day
        series_end = end_day or last_history_day

        if not series_start or not series_end:
            raise ValueError(f"Item history is missing valid consumption_date values: {item_id}")
        if series_end < series_start:
            raise ValueError("end_day must be on or after start_day")

        history_by_day = {
            row["consumption_date"]: int(float(row["actual_sale"] or 0))
            for row in history_rows
            if row["consumption_date"] is not None
        }

        sim_input_his: list[dict[str, Any]] = []
        current_day = series_start
        while current_day <= series_end:
            sim_input_his.append(
                {
                    "item_id": item_id,
                    "actual_sale": history_by_day.get(current_day, 0),
                    "day": current_day.isoformat(),
                }
            )
            current_day += timedelta(days=1)

        return {
            "sim_input_his": sim_input_his,
            "forecast_periods": forecast_periods,
            "mode": mode,
            "local_model": local_model,
            "season_length": season_length,
            "freq": freq,
        }


def update_purchase_suggestions(suggestions: list[dict[str, Any]], database: str | None = None) -> int:
    if not suggestions:
        return 0

    with connection(database) as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                UPDATE items
                SET purchase_suggestion = %(purchase_qty)s,
                    purch_sugg_creation_date = %(current_datetime)s
                WHERE id = %(item_id)s
                """,
                suggestions,
            )
        conn.commit()
        return sum(1 for s in suggestions if s.get("purchase_qty", 0) is not None)


_POSITIVE_ORDER_QTY_SQL = (
    "COALESCE(order_lines.qty_override, order_lines.qty_suggested, 0) > 0"
)


def _resolve_item_ids_for_order_scope(
    conn: MySQLConnection,
    *,
    item_ids: list[int] | None = None,
    source_order_id: int | None = None,
) -> list[int] | None:
    if source_order_id is not None:
        _ensure_orders_tables(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT order_lines.item_id
                FROM order_lines
                WHERE order_lines.order_id = %s
                  AND order_lines.item_id IS NOT NULL
                  AND COALESCE(order_lines.deleted, 0) = 0
                ORDER BY order_lines.item_id
                """,
                (source_order_id,),
            )
            return [int(row[0]) for row in cursor.fetchall() if row and row[0] is not None]
    if item_ids:
        return sorted({int(item_id) for item_id in item_ids})
    return None


def reset_purchase_suggestions(
    database: str | None = None,
    item_ids: list[int] | None = None,
    source_order_id: int | None = None,
) -> int:
    with connection(database) as conn:
        scoped_ids = _resolve_item_ids_for_order_scope(
            conn,
            item_ids=item_ids,
            source_order_id=source_order_id,
        )
        with conn.cursor() as cursor:
            if scoped_ids is None:
                cursor.execute("UPDATE items SET purchase_suggestion = 0")
            elif not scoped_ids:
                return 0
            else:
                placeholders = ",".join(["%s"] * len(scoped_ids))
                cursor.execute(
                    f"UPDATE items SET purchase_suggestion = 0 WHERE id IN ({placeholders})",
                    scoped_ids,
                )
            count = cursor.rowcount
        conn.commit()
        return count


def _migrate_order_lines_comment_column(conn: MySQLConnection) -> None:
    columns = {column.name for column in get_columns(conn, "order_lines")}
    if "order_comments" in columns:
        return
    with conn.cursor() as cursor:
        if "order_comment" in columns:
            cursor.execute(
                "ALTER TABLE order_lines CHANGE COLUMN order_comment order_comments VARCHAR(512) NULL"
            )
        elif "comment" in columns:
            cursor.execute(
                "ALTER TABLE order_lines CHANGE COLUMN comment order_comments VARCHAR(512) NULL"
            )
    conn.commit()


def _ensure_orders_tables(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                location_id INT NULL,
                location_order_from_id INT NULL,
                order_date DATE NULL,
                order_status VARCHAR(20) NULL,
                user_id INT NULL,
                description VARCHAR(255) NULL,
                created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                est_delivery_date DATE NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS order_lines (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                order_id INT NULL,
                item_id INT NOT NULL,
                order_date DATETIME NULL,
                est_delivery_date DATE NULL,
                qty_suggested INT NULL,
                qty_override INT NULL,
                order_from_location_id INT NOT NULL DEFAULT 0,
                order_comments VARCHAR(512) NULL,
                po VARCHAR(200) NULL,
                deleted TINYINT NOT NULL DEFAULT 0,
                status VARCHAR(50) NULL,
                progress VARCHAR(50) NULL,
                assigned_to INT NULL,
                INDEX idx_order_lines_order_id (order_id),
                INDEX idx_order_lines_item_id (item_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    conn.commit()
    try:
        _migrate_order_lines_comment_column(conn)
    except ValueError:
        pass
    try:
        _migrate_po_to_order_lines(conn)
    except ValueError:
        pass
    try:
        _migrate_order_lines_deleted_column(conn)
    except ValueError:
        pass
    try:
        _migrate_order_lines_status_column(conn)
    except ValueError:
        pass
    try:
        _migrate_order_lines_progress_column(conn)
    except ValueError:
        pass
    try:
        _migrate_order_lines_assigned_to_column(conn)
    except ValueError:
        pass
    try:
        _migrate_order_lines_purch_sugg_confirmed_column(conn)
    except ValueError:
        pass
    try:
        _migrate_order_lines_manual_columns(conn)
    except ValueError:
        pass


def create_order_from_purchase_suggestions(
    database: str | None = None,
    user_id: int | None = None,
    description: str | None = None,
    item_ids: list[int] | None = None,
    source_order_id: int | None = None,
) -> dict[str, Any]:
    with connection(database) as conn:
        item_columns = {column.name for column in ensure_table_exists(conn, "items")}
        if "purchase_suggestion" not in item_columns:
            raise ValueError("items table must include purchase_suggestion to create an order")
        est_delivery_expr = (
            "CASE "
            "WHEN items.del_time IS NULL THEN NULL "
            "ELSE DATE_ADD(CURDATE(), INTERVAL GREATEST(CAST(items.del_time AS SIGNED), 0) DAY) "
            "END"
            if "del_time" in item_columns
            else "NULL"
        )
        _ensure_orders_tables(conn)
        scoped_ids = _resolve_item_ids_for_order_scope(
            conn,
            item_ids=item_ids,
            source_order_id=source_order_id,
        )
        scope_sql = ""
        scope_params: tuple[Any, ...] = ()
        if scoped_ids is not None:
            if not scoped_ids:
                return {"order_id": None, "line_count": 0}
            placeholders = ",".join(["%s"] * len(scoped_ids))
            scope_sql = f" AND items.id IN ({placeholders})"
            scope_params = tuple(scoped_ids)
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO orders (
                    order_date,
                    order_status,
                    user_id,
                    description,
                    created_at,
                    updated_at
                )
                VALUES (CURDATE(), 'official_suggestion', %s, %s, NOW(), NOW())
                """,
                (user_id, description or "Official innkaupatillaga"),
            )
            order_id = cursor.lastrowid
            cursor.execute(
                f"""
                INSERT INTO order_lines (
                    order_id,
                    item_id,
                    order_date,
                    est_delivery_date,
                    qty_suggested,
                    qty_override,
                    order_from_location_id,
                    order_comments
                )
                SELECT
                    %s AS order_id,
                    items.id AS item_id,
                    NOW() AS order_date,
                    {est_delivery_expr} AS est_delivery_date,
                    CAST(ROUND(items.purchase_suggestion) AS SIGNED) AS qty_suggested,
                    CAST(ROUND(items.purchase_suggestion) AS SIGNED) AS qty_override,
                    0 AS order_from_location_id,
                    NULL AS order_comments
                FROM items
                WHERE COALESCE(items.purchase_suggestion, 0) > 0
                {scope_sql}
                """,
                (order_id, *scope_params),
            )
            line_count = cursor.rowcount
            if line_count == 0:
                cursor.execute("DELETE FROM orders WHERE id = %s", (order_id,))
                order_id = None
        conn.commit()
        return {"order_id": order_id, "line_count": line_count}


def list_orders(database: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with connection(database) as conn:
        _ensure_orders_tables(conn)
        return _get_rows(
            conn,
            f"""
            SELECT
                orders.*,
                COUNT(order_lines.id) AS line_count,
                COALESCE(SUM(COALESCE(order_lines.qty_override, order_lines.qty_suggested)), 0) AS total_qty
            FROM orders
            LEFT JOIN order_lines
                ON orders.id = order_lines.order_id
                AND COALESCE(order_lines.deleted, 0) = 0
                AND {_POSITIVE_ORDER_QTY_SQL}
            GROUP BY orders.id
            ORDER BY COALESCE(orders.created_at, orders.order_date) DESC, orders.id DESC
            LIMIT %s
            """,
            (limit,),
        )


def _sql_quote_identifier(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def _sql_item_col(item_cols: set[str], name: str, alias: str | None = None) -> str:
    out = alias or name
    out_sql = _sql_quote_identifier(out)
    if name in item_cols:
        return f"items.{_sql_quote_identifier(name)} AS {out_sql}"
    return f"NULL AS {out_sql}"


_ORDER_LINE_EXPLICIT_ITEM_COLS = {
    "id",
    "item_number",
    "description",
    "stock_level",
    "qty_on_order",
    "purchase_suggestion",
    "buy_freq",
    "del_time",
    "location_name",
    "location",
    "last_year_usage",
    "num_move_last_year",
    "comment",
    "active_flag",
    "purch_sugg_creation_date",
    "vendor_name",
    "purchasing_method",
    "unit_cost",
    "price",
}


def _order_line_display_item_fields(conn: MySQLConnection) -> str:
    item_cols = {column.name for column in get_columns(conn, "items")}
    ol_cols = {column.name for column in get_columns(conn, "order_lines")}
    has_manual = "is_manual" in ol_cols

    if "location_name" in item_cols:
        location_name_sql = "items.location_name AS location_name"
    elif "location" in item_cols:
        location_name_sql = "items.location AS location_name"
    else:
        location_name_sql = "NULL AS location_name"

    unit_parts: list[str] = []
    if "unit_cost" in item_cols:
        unit_parts.append("items.unit_cost")
    if "price" in item_cols:
        unit_parts.append("items.price")
    if "manual_unit_price" in ol_cols:
        unit_parts.append("order_lines.manual_unit_price")
    if len(unit_parts) >= 2:
        unit_sql = f"COALESCE({', '.join(unit_parts)})"
    elif unit_parts:
        unit_sql = unit_parts[0]
    else:
        unit_sql = "NULL"

    vendor_parts = ["vo.vendor_name", "items.vendor_name"]
    if "manual_vendor_name" in ol_cols:
        vendor_parts.append("order_lines.manual_vendor_name")
    vendor_sql = f"COALESCE({', '.join(vendor_parts)}) AS vendor_name"

    id_sql = (
        "COALESCE(items.id, -order_lines.id) AS id"
        if has_manual
        else "items.id AS id"
    )
    if has_manual and "manual_item_number" in ol_cols:
        item_number_sql = (
            "COALESCE(items.item_number, order_lines.manual_item_number) AS item_number"
        )
    else:
        item_number_sql = "items.item_number AS item_number"
    if has_manual and "manual_description" in ol_cols:
        description_sql = (
            "COALESCE(items.description, order_lines.manual_description) AS description"
        )
    else:
        description_sql = "items.description AS description"

    if has_manual:
        manual_sql = (
            "order_lines.is_manual AS order_is_manual, "
            "order_lines.manual_item_number AS order_manual_item_number, "
            "order_lines.manual_description AS order_manual_description, "
            "order_lines.manual_vendor_name AS order_manual_vendor_name, "
            "order_lines.manual_unit_price AS order_manual_unit_price"
        )
    else:
        manual_sql = (
            "0 AS order_is_manual, "
            "NULL AS order_manual_item_number, "
            "NULL AS order_manual_description, "
            "NULL AS order_manual_vendor_name, "
            "NULL AS order_manual_unit_price"
        )

    parts = [
        id_sql,
        item_number_sql,
        description_sql,
        _sql_item_col(item_cols, "stock_level"),
        _sql_item_col(item_cols, "qty_on_order"),
        _sql_item_col(item_cols, "purchase_suggestion"),
        _sql_item_col(item_cols, "buy_freq"),
        _sql_item_col(item_cols, "del_time"),
        location_name_sql,
        _sql_item_col(item_cols, "location"),
        _sql_item_col(item_cols, "last_year_usage"),
        _sql_item_col(item_cols, "num_move_last_year"),
        _sql_item_col(item_cols, "comment"),
        _sql_item_col(item_cols, "active_flag"),
        _sql_item_col(item_cols, "purch_sugg_creation_date"),
        "items.vendor_name AS item_vendor_name",
        vendor_sql,
        "vo.set_at AS vendor_override_set_at",
        "items.purchasing_method AS item_purchasing_method",
        "COALESCE(pmo.purchasing_method, items.purchasing_method) AS purchasing_method",
        "pmo.set_at AS purchasing_method_override_set_at",
        f"{unit_sql} AS unit_cost",
        f"{unit_sql} AS unit_price",
        f"{unit_sql} AS price",
        manual_sql,
    ]
    for col_name in sorted(item_cols):
        if col_name not in _ORDER_LINE_EXPLICIT_ITEM_COLS:
            parts.append(_sql_item_col(item_cols, col_name))
    return ",\n                ".join(parts)


def _order_items_select_sql(vendor_override_days: int, item_fields: str) -> str:
    return f"""
            SELECT
                {item_fields},
                orders.id AS order_header_id,
                orders.location_id AS order_location_id,
                orders.location_order_from_id AS order_location_order_from_id,
                orders.order_date AS order_header_order_date,
                orders.order_status AS order_status,
                orders.user_id AS order_user_id,
                orders.description AS order_description,
                orders.created_at AS order_created_at,
                orders.updated_at AS order_updated_at,
                orders.est_delivery_date AS order_header_est_delivery_date,
                order_lines.id AS order_line_id,
                order_lines.order_id AS order_id,
                order_lines.item_id AS order_item_id,
                order_lines.order_date AS order_order_date,
                order_lines.est_delivery_date AS order_est_delivery_date,
                order_lines.qty_suggested AS order_qty_suggested,
                order_lines.qty_override AS order_qty_override,
                order_lines.order_from_location_id AS order_from_location_id,
                order_lines.order_comments AS order_comments,
                order_lines.po AS order_po,
                order_lines.deleted AS order_deleted,
                order_lines.status AS order_line_status,
                order_lines.progress AS order_line_progress,
                order_lines.assigned_to AS order_assigned_to,
                order_lines.purch_sugg_confirmed AS order_purch_sugg_confirmed
            FROM {{from_clause}}
            {_item_override_join_sql(vendor_override_days)}
            {{where_clause}}
            {{order_clause}}
            LIMIT %s OFFSET %s
            """


def list_order_items(
    order_id: int,
    database: str | None = None,
    limit: int = 20000,
    offset: int = 0,
    order_lines_only: bool = True,
    stock_out: bool = False,
) -> list[dict[str, Any]]:
    with connection(database) as conn:
        ensure_table_exists(conn, "items")
        _ensure_orders_tables(conn)
        _ensure_vendor_overrides_table(conn)
        _ensure_purchasing_method_overrides_table(conn)
        vendor_override_days = _get_vendor_override_days(database)
        item_fields = _order_line_display_item_fields(conn)
        select_sql = _order_items_select_sql(vendor_override_days, item_fields)
        if order_lines_only:
            query = select_sql.format(
                from_clause="""
            order_lines
            INNER JOIN orders ON orders.id = order_lines.order_id
            LEFT JOIN items ON items.id = order_lines.item_id""",
                where_clause=(
                    "WHERE order_lines.order_id = %s"
                    " AND COALESCE(order_lines.deleted, 0) = 0"
                    f" AND {_POSITIVE_ORDER_QTY_SQL}"
                ),
                order_clause="ORDER BY order_lines.id",
            )
            params = (order_id, limit, offset)
        else:
            stock_filter = (
                " AND (items.stock_level <= 0 OR items.stock_level IS NULL)"
                if stock_out
                else ""
            )
            query = select_sql.format(
                from_clause="""
            items
            LEFT JOIN order_lines
                ON order_lines.item_id = items.id
                AND order_lines.order_id = %s
                AND COALESCE(order_lines.deleted, 0) = 0
            LEFT JOIN orders ON orders.id = %s""",
                where_clause=f"WHERE 1=1{stock_filter}",
                order_clause="ORDER BY items.id",
            )
            params = (order_id, order_id, limit, offset)
        return _get_rows(conn, query, params)


def _normalize_item_number(item_number: str | None) -> str:
    return (item_number or "").strip()


def _item_number_where_sql(column: str = "items.item_number") -> str:
    return f"TRIM({column}) = %s"


def get_item_by_item_number(item_number: str, database: str | None = None) -> dict[str, Any] | None:
    pn = _normalize_item_number(item_number)
    if not pn:
        return None
    with connection(database) as conn:
        ensure_table_exists(conn, "items")
        _ensure_vendor_overrides_table(conn)
        _ensure_purchasing_method_overrides_table(conn)
        vendor_override_days = _get_vendor_override_days(database)
        row = _get_single_row(
            conn,
            (
                f"SELECT {_item_override_select_fields()} "
                f"FROM items {_item_override_join_sql(vendor_override_days)} "
                f"WHERE {_item_number_where_sql()} LIMIT 1"
            ),
            (pn,),
        )
        return normalize_row(row) if row else None


def _fetch_order_item_row(
    conn: MySQLConnection,
    order_id: int,
    order_line_id: int,
    database: str | None,
) -> dict[str, Any] | None:
    ensure_table_exists(conn, "items")
    _ensure_orders_tables(conn)
    _ensure_vendor_overrides_table(conn)
    _ensure_purchasing_method_overrides_table(conn)
    vendor_override_days = _get_vendor_override_days(database)
    item_fields = _order_line_display_item_fields(conn)
    query = f"""
            SELECT
                {item_fields},
                orders.id AS order_header_id,
                orders.location_id AS order_location_id,
                orders.location_order_from_id AS order_location_order_from_id,
                orders.order_date AS order_header_order_date,
                orders.order_status AS order_status,
                orders.user_id AS order_user_id,
                orders.description AS order_description,
                orders.created_at AS order_created_at,
                orders.updated_at AS order_updated_at,
                orders.est_delivery_date AS order_header_est_delivery_date,
                order_lines.id AS order_line_id,
                order_lines.order_id AS order_id,
                order_lines.item_id AS order_item_id,
                order_lines.order_date AS order_order_date,
                order_lines.est_delivery_date AS order_est_delivery_date,
                order_lines.qty_suggested AS order_qty_suggested,
                order_lines.qty_override AS order_qty_override,
                order_lines.order_from_location_id AS order_from_location_id,
                order_lines.order_comments AS order_comments,
                order_lines.po AS order_po,
                order_lines.deleted AS order_deleted,
                order_lines.status AS order_line_status,
                order_lines.progress AS order_line_progress,
                order_lines.assigned_to AS order_assigned_to,
                order_lines.purch_sugg_confirmed AS order_purch_sugg_confirmed
            FROM order_lines
            INNER JOIN orders ON orders.id = order_lines.order_id
            LEFT JOIN items ON items.id = order_lines.item_id
            {_item_override_join_sql(vendor_override_days)}
            WHERE order_lines.order_id = %s
              AND order_lines.id = %s
              AND COALESCE(order_lines.deleted, 0) = 0
            LIMIT 1
            """
    row = _get_single_row(conn, query, (order_id, order_line_id))
    return normalize_row(row) if row else None


def add_order_line(
    order_id: int,
    *,
    item_number: str,
    qty: int,
    description: str | None = None,
    vendor_name: str | None = None,
    unit_price: float | None = None,
    database: str | None = None,
) -> dict[str, Any]:
    pn = _normalize_item_number(item_number)
    if not pn:
        raise ValueError("item_number is required")
    try:
        qty_int = int(qty)
    except (TypeError, ValueError) as exc:
        raise ValueError("qty must be a positive integer") from exc
    if qty_int <= 0:
        raise ValueError("qty must be a positive integer")

    with connection(database) as conn:
        _ensure_orders_tables(conn)
        ensure_table_exists(conn, "items")
        order = _get_single_row(conn, "SELECT id FROM orders WHERE id = %s", (order_id,))
        if not order:
            raise ValueError(f"Order {order_id} not found")

        item = _get_single_row(
            conn,
            f"SELECT id, del_time FROM items WHERE {_item_number_where_sql()} LIMIT 1",
            (pn,),
        )
        item_columns = {column.name for column in ensure_table_exists(conn, "items")}
        est_delivery_expr = (
            "CASE "
            "WHEN items.del_time IS NULL THEN NULL "
            "ELSE DATE_ADD(CURDATE(), INTERVAL GREATEST(CAST(items.del_time AS SIGNED), 0) DAY) "
            "END"
            if "del_time" in item_columns
            else "NULL"
        )

        with conn.cursor() as cursor:
            if item:
                cursor.execute(
                    f"""
                    INSERT INTO order_lines (
                        order_id,
                        item_id,
                        is_manual,
                        order_date,
                        est_delivery_date,
                        qty_suggested,
                        qty_override,
                        order_from_location_id
                    )
                    SELECT
                        %s AS order_id,
                        items.id AS item_id,
                        0 AS is_manual,
                        NOW() AS order_date,
                        {est_delivery_expr} AS est_delivery_date,
                        %s AS qty_suggested,
                        %s AS qty_override,
                        0 AS order_from_location_id
                    FROM items
                    WHERE items.id = %s
                    """,
                    (order_id, qty_int, qty_int, item["id"]),
                )
            else:
                desc = (description or "").strip()
                if not desc:
                    raise ValueError("description is required when item_number is not in catalog")
                vendor = (vendor_name or "").strip() or None
                price = float(unit_price) if unit_price is not None and unit_price != "" else None
                cursor.execute(
                    """
                    INSERT INTO order_lines (
                        order_id,
                        item_id,
                        is_manual,
                        manual_item_number,
                        manual_description,
                        manual_vendor_name,
                        manual_unit_price,
                        order_date,
                        qty_suggested,
                        qty_override,
                        order_from_location_id
                    )
                    VALUES (%s, NULL, 1, %s, %s, %s, %s, NOW(), %s, %s, 0)
                    """,
                    (order_id, pn, desc, vendor, price, qty_int, qty_int),
                )
            line_id = cursor.lastrowid
        conn.commit()
        row = _fetch_order_item_row(conn, order_id, line_id, database)
        if not row:
            raise ValueError("Failed to load created order line")
        return {"order_line_id": line_id, "row": row}


def _normalize_order_line_progress(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "Not Started"
    return str(value).strip()


def merge_order_lines_from_order(
    target_order_id: int,
    source_order_id: int,
    *,
    progress_statuses: list[str] | None = None,
    set_progress: str | None = None,
    database: str | None = None,
) -> dict[str, Any]:
    """Copy/add lines from source order into target (comments, progress, qty_override)."""
    if target_order_id == source_order_id:
        raise ValueError("Source and target order must differ")

    include_progress = (
        {_normalize_order_line_progress(p) for p in progress_statuses}
        if progress_statuses
        else None
    )
    override_progress = (
        _normalize_order_line_progress(set_progress) if set_progress else None
    )

    with connection(database) as conn:
        _ensure_orders_tables(conn)
        for oid in (target_order_id, source_order_id):
            order = _get_single_row(conn, "SELECT id FROM orders WHERE id = %s", (oid,))
            if not order:
                raise ValueError(f"Order {oid} not found")

        source_lines = _get_rows(
            conn,
            """
            SELECT
                id,
                item_id,
                is_manual,
                manual_item_number,
                manual_description,
                manual_vendor_name,
                manual_unit_price,
                est_delivery_date,
                qty_suggested,
                qty_override,
                order_comments,
                progress,
                order_from_location_id,
                po,
                assigned_to,
                status
            FROM order_lines
            WHERE order_id = %s
              AND COALESCE(deleted, 0) = 0
            ORDER BY id
            """,
            (source_order_id,),
        )

        added = 0
        updated = 0
        skipped = 0

        with conn.cursor() as cursor:
            for line in source_lines:
                line_progress = _normalize_order_line_progress(line.get("progress"))
                if include_progress is not None and line_progress not in include_progress:
                    skipped += 1
                    continue

                qty = line.get("qty_override")
                if qty is None:
                    qty = line.get("qty_suggested")
                try:
                    qty_int = int(round(float(qty or 0)))
                except (TypeError, ValueError):
                    qty_int = 0

                progress_val = override_progress or line_progress
                comments = line.get("order_comments")

                item_id = line.get("item_id")
                is_manual = int(line.get("is_manual") or 0)
                manual_pn = (line.get("manual_item_number") or "").strip() or None

                existing_id = None
                if item_id is not None:
                    cursor.execute(
                        """
                        SELECT id FROM order_lines
                        WHERE order_id = %s
                          AND item_id = %s
                          AND COALESCE(deleted, 0) = 0
                        LIMIT 1
                        """,
                        (target_order_id, item_id),
                    )
                    row = cursor.fetchone()
                    existing_id = row[0] if row else None
                elif is_manual and manual_pn:
                    cursor.execute(
                        """
                        SELECT id FROM order_lines
                        WHERE order_id = %s
                          AND COALESCE(is_manual, 0) = 1
                          AND manual_item_number = %s
                          AND COALESCE(deleted, 0) = 0
                        LIMIT 1
                        """,
                        (target_order_id, manual_pn),
                    )
                    row = cursor.fetchone()
                    existing_id = row[0] if row else None

                if existing_id:
                    cursor.execute(
                        """
                        UPDATE order_lines
                        SET qty_override = %s,
                            progress = %s,
                            order_comments = %s
                        WHERE id = %s
                        """,
                        (qty_int, progress_val, comments, existing_id),
                    )
                    updated += 1
                else:
                    cursor.execute(
                        """
                        INSERT INTO order_lines (
                            order_id,
                            item_id,
                            is_manual,
                            manual_item_number,
                            manual_description,
                            manual_vendor_name,
                            manual_unit_price,
                            order_date,
                            est_delivery_date,
                            qty_suggested,
                            qty_override,
                            order_from_location_id,
                            order_comments,
                            progress,
                            po,
                            assigned_to,
                            status
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            target_order_id,
                            item_id,
                            is_manual,
                            line.get("manual_item_number"),
                            line.get("manual_description"),
                            line.get("manual_vendor_name"),
                            line.get("manual_unit_price"),
                            line.get("est_delivery_date"),
                            qty_int,
                            qty_int,
                            int(line.get("order_from_location_id") or 0),
                            comments,
                            progress_val,
                            line.get("po"),
                            line.get("assigned_to"),
                            line.get("status"),
                        ),
                    )
                    added += 1

        conn.commit()

    return {
        "target_order_id": target_order_id,
        "source_order_id": source_order_id,
        "added": added,
        "updated": updated,
        "skipped": skipped,
    }


# Deep Dive Opt defaults (see purch_sys_customers/src/config/deepDiveDefaults.js)
DEEP_DIVE_FIXED_SHIPPING_USD = 90.0
DEEP_DIVE_INTEREST_RATE_PCT = 18.0
SIM_OPTIMAL_PLAN_VIEW = "v_sim_optimal_plan"
SIM_OPTIMAL_PLAN_DETAIL_VIEW = "v_sim_optimal_plan_detail"
SIM_OPTIMAL_PLAN_DAILY_TABLE = "sim_optimal_plan_daily"
SIM_OPTIMAL_PLAN_DAILY_VIEW = "v_sim_optimal_plan_by_day"


def _items_unit_cost_expr(conn: MySQLConnection, alias: str = "i") -> str:
    """COALESCE(unit_cost, price, 0) — skips zero so price can backfill."""
    item_cols = {column.name for column in get_columns(conn, "items")}
    parts: list[str] = []
    if "unit_cost" in item_cols:
        parts.append(f"NULLIF({alias}.unit_cost, 0)")
    if "price" in item_cols:
        parts.append(f"NULLIF({alias}.price, 0)")
    if not parts:
        return "0"
    if len(parts) == 1:
        return f"COALESCE({parts[0]}, 0)"
    return f"COALESCE({parts[0]}, {parts[1]}, 0)"


def _coerce_sql_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return date.fromisoformat(text[:10])
    return None


def _sim_optimal_plan_cost_exprs(conn: MySQLConnection, alias: str = "i") -> tuple[str, str, str]:
    unit_cost = _items_unit_cost_expr(conn, alias)
    holding_rate = DEEP_DIVE_INTEREST_RATE_PCT / 100.0
    shipping = DEEP_DIVE_FIXED_SHIPPING_USD
    inv_value = f"(COALESCE(sr.inv, 0) * ({unit_cost}))"
    inventory_cost = f"{inv_value} * ({holding_rate} / 365)"
    fixed_shipping = (
        f"CASE WHEN COALESCE(sr.deliveries, 0) > 0 THEN {shipping} ELSE 0 END"
    )
    return inv_value, inventory_cost, fixed_shipping


def _ensure_sim_result_indexes(conn: MySQLConnection) -> None:
    ensure_table_exists(conn, "sim_result")
    indexes = {
        "idx_sim_result_sim_date": "sim_date",
        "idx_sim_result_item_sim_date": "item_id, sim_date",
    }
    with conn.cursor() as cursor:
        cursor.execute("SHOW INDEX FROM sim_result")
        existing = {row[2] for row in cursor.fetchall()}
        for name, columns in indexes.items():
            if name not in existing:
                cursor.execute(
                    f"CREATE INDEX {quote_ident(name)} ON sim_result ({columns})"
                )
    conn.commit()


def _ensure_sim_optimal_plan_daily_table(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {quote_ident(SIM_OPTIMAL_PLAN_DAILY_TABLE)} (
                dags DATE NOT NULL PRIMARY KEY,
                inv_value DECIMAL(18, 2) NOT NULL DEFAULT 0,
                inventory_cost DECIMAL(18, 6) NOT NULL DEFAULT 0,
                fixed_shipping_cost DECIMAL(18, 2) NOT NULL DEFAULT 0
            ) ENGINE=InnoDB
            """
        )
    conn.commit()


def refresh_sim_optimal_plan_daily(conn: MySQLConnection, dates: list[Any] | None = None) -> int:
    """Rebuild daily aggregates. If dates given, refresh only those days (fast after sim upsert)."""
    ensure_table_exists(conn, "sim_result")
    ensure_table_exists(conn, "items")
    _ensure_sim_optimal_plan_daily_table(conn)
    inv_value, inventory_cost, fixed_shipping = _sim_optimal_plan_cost_exprs(conn, "i")
    aggregate_select = f"""
        SELECT
            sr.sim_date AS dags,
            SUM({inv_value}) AS inv_value,
            SUM({inventory_cost}) AS inventory_cost,
            SUM({fixed_shipping}) AS fixed_shipping_cost
        FROM sim_result sr
        INNER JOIN items i ON i.id = sr.item_id
    """
    with conn.cursor() as cursor:
        if not dates:
            cursor.execute(f"TRUNCATE TABLE {quote_ident(SIM_OPTIMAL_PLAN_DAILY_TABLE)}")
            cursor.execute(
                f"""
                INSERT INTO {quote_ident(SIM_OPTIMAL_PLAN_DAILY_TABLE)}
                    (dags, inv_value, inventory_cost, fixed_shipping_cost)
                {aggregate_select}
                GROUP BY sr.sim_date
                """
            )
        else:
            unique_dates = sorted(
                {d for d in (_coerce_sql_date(d) for d in dates) if d is not None}
            )
            if not unique_dates:
                conn.commit()
                return 0
            placeholders = ",".join(["%s"] * len(unique_dates))
            cursor.execute(
                f"DELETE FROM {quote_ident(SIM_OPTIMAL_PLAN_DAILY_TABLE)} WHERE dags IN ({placeholders})",
                unique_dates,
            )
            cursor.execute(
                f"""
                INSERT INTO {quote_ident(SIM_OPTIMAL_PLAN_DAILY_TABLE)}
                    (dags, inv_value, inventory_cost, fixed_shipping_cost)
                {aggregate_select}
                WHERE sr.sim_date IN ({placeholders})
                GROUP BY sr.sim_date
                """,
                unique_dates,
            )
        row_count = cursor.rowcount
    conn.commit()
    return row_count


def ensure_sim_optimal_plan_view(conn: MySQLConnection) -> None:
    """
    Daily cost view from latest sim_result + items (Deep Dive / optimal plan).

    - inv_value: inv × unit cost
    - inventory_cost: daily holding cost @ 18% árlega (18/365 per day)
    - fixed_shipping_cost: 90 USD on days with deliveries > 0 in sim_result
    """
    _ensure_sim_result_indexes(conn)
    ensure_table_exists(conn, "sim_result")
    ensure_table_exists(conn, "items")
    inv_value, inventory_cost, fixed_shipping = _sim_optimal_plan_cost_exprs(conn, "i")
    detail_view_sql = f"""
        CREATE OR REPLACE VIEW {quote_ident(SIM_OPTIMAL_PLAN_DETAIL_VIEW)} AS
        SELECT
            sr.item_id AS item_id,
            sr.sim_date AS dags,
            {inv_value} AS inv_value,
            {inventory_cost} AS inventory_cost,
            {fixed_shipping} AS fixed_shipping_cost
        FROM sim_result sr
        INNER JOIN items i ON i.id = sr.item_id
    """
    daily_view_sql = f"""
        CREATE OR REPLACE VIEW {quote_ident(SIM_OPTIMAL_PLAN_VIEW)} AS
        SELECT
            dags,
            inv_value,
            inventory_cost,
            fixed_shipping_cost
        FROM {quote_ident(SIM_OPTIMAL_PLAN_DAILY_TABLE)}
    """
    daily_alias_sql = f"""
        CREATE OR REPLACE VIEW {quote_ident(SIM_OPTIMAL_PLAN_DAILY_VIEW)} AS
        SELECT
            dags,
            inv_value,
            inventory_cost,
            fixed_shipping_cost
        FROM {quote_ident(SIM_OPTIMAL_PLAN_DAILY_TABLE)}
    """
    with conn.cursor() as cursor:
        cursor.execute(detail_view_sql)
        _ensure_sim_optimal_plan_daily_table(conn)
        cursor.execute(daily_view_sql)
        cursor.execute(daily_alias_sql)
    refresh_sim_optimal_plan_daily(conn)
    conn.commit()


def upsert_sim_result(rows: list[dict[str, Any]], database: str | None = None) -> int:
    if not rows:
        return 0

    item_ids = list({row["item_id"] for row in rows})

    with connection(database) as conn:
        with conn.cursor() as cursor:
            fmt = ",".join(["%s"] * len(item_ids))
            cursor.execute(
                f"SELECT DISTINCT sim_date FROM sim_result WHERE item_id IN ({fmt})",
                item_ids,
            )
            dates_before = {
                d for d in (_coerce_sql_date(r[0]) for r in cursor.fetchall()) if d is not None
            }
            cursor.execute(f"DELETE FROM sim_result WHERE item_id IN ({fmt})", item_ids)

            cursor.executemany(
                """
                INSERT INTO sim_result
                    (item_id, inv, purchase_qty, deliveries, lost_sale, expired, sim_date, forecast, actual_sale)
                VALUES
                    (%(item_id)s, %(inv)s, %(purchase_qty)s, %(deliveries)s, %(lost_sale)s,
                     %(expired)s, %(sim_date)s, %(forecast)s, %(actual_sale)s)
                """,
                rows,
            )
        dates_after = {
            d
            for d in (_coerce_sql_date(row.get("sim_date")) for row in rows)
            if d is not None
        }
        refresh_sim_optimal_plan_daily(conn, dates=list(dates_before | dates_after))
        return len(rows)


FORECAST_RESULT_TABLE = "forecast_result"


def _ensure_forecast_result_table(conn: MySQLConnection) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {quote_ident(FORECAST_RESULT_TABLE)} (
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
        )
    conn.commit()


def upsert_forecast_result(rows: list[dict[str, Any]], database: str | None = None) -> int:
    if not rows:
        return 0

    prepared: list[dict[str, Any]] = []
    for row in rows:
        forecast_date = _coerce_sql_date(row.get("forecast_date"))
        if forecast_date is None or row.get("item_id") is None:
            continue
        prepared.append(
            {
                "item_id": int(row["item_id"]),
                "forecast_date": forecast_date,
                "forecast": row.get("forecast"),
                "upper_70": row.get("upper_70"),
                "upper_90": row.get("upper_90"),
                "upper_95": row.get("upper_95"),
                "model_used": row.get("model_used"),
                "freq": row.get("freq") or "D",
            }
        )

    if not prepared:
        return 0

    item_ids = list({row["item_id"] for row in prepared})

    with connection(database) as conn:
        _ensure_forecast_result_table(conn)
        with conn.cursor() as cursor:
            fmt = ",".join(["%s"] * len(item_ids))
            cursor.execute(
                f"DELETE FROM {quote_ident(FORECAST_RESULT_TABLE)} WHERE item_id IN ({fmt})",
                item_ids,
            )
            cursor.executemany(
                f"""
                INSERT INTO {quote_ident(FORECAST_RESULT_TABLE)}
                    (item_id, forecast_date, forecast, upper_70, upper_90, upper_95, model_used, freq)
                VALUES
                    (%(item_id)s, %(forecast_date)s, %(forecast)s, %(upper_70)s,
                     %(upper_90)s, %(upper_95)s, %(model_used)s, %(freq)s)
                """,
                prepared,
            )
        conn.commit()
        return len(prepared)


def get_forecast_result(
    item_id: int,
    database: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    with connection(database) as conn:
        _ensure_forecast_result_table(conn)
        return _get_rows(
            conn,
            f"""
            SELECT item_id, forecast_date, forecast, upper_70, upper_90, upper_95,
                   model_used, freq, run_at
            FROM {quote_ident(FORECAST_RESULT_TABLE)}
            WHERE item_id = %s
            ORDER BY forecast_date
            LIMIT %s
            """,
            (item_id, limit),
        )


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            normalized[key] = float(value)
        else:
            normalized[key] = value
    return normalized


def _get_single_row(
    conn: MySQLConnection,
    query: str,
    params: tuple[Any, ...],
) -> dict[str, Any] | None:
    with conn.cursor(dictionary=True) as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    return normalize_row(row) if row else None


def _get_rows(
    conn: MySQLConnection,
    query: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    with conn.cursor(dictionary=True) as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    return [normalize_row(row) for row in rows]


def _to_number(value: Any, default: int | float = 0) -> int | float:
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return default
        return int(parsed) if parsed.is_integer() else parsed
    return value


def filter_payload(payload: dict[str, Any], valid_column_names: Any) -> dict[str, Any]:
    valid = set(valid_column_names)
    return {key: value for key, value in payload.items() if key in valid}


def get_lookup_options(
    table_name: str,
    value_column: str,
    label_column: str | None = None,
    database: str | None = None,
) -> list[dict[str, str]]:
    if table_name == "vendor_info":
        return _get_vendor_info_lookup_options(value_column, label_column, database)

    label_col = label_column or value_column
    with connection(database) as conn:
        columns = ensure_table_exists(conn, table_name)
        valid_names = {c.name for c in columns}
        if value_column not in valid_names:
            raise ValueError(f"Column not found: {value_column}")
        if label_col not in valid_names:
            raise ValueError(f"Column not found: {label_col}")

        query = (
            f"SELECT DISTINCT {quote_ident(value_column)} AS value, "
            f"{quote_ident(label_col)} AS label "
            f"FROM {quote_ident(table_name)} "
            f"WHERE {quote_ident(value_column)} IS NOT NULL "
            f"ORDER BY {quote_ident(label_col)}"
        )
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    return [
        {"value": str(row["value"]), "label": str(row["label"])}
        for row in rows
    ]


def _get_vendor_info_lookup_options(
    value_column: str,
    label_column: str | None,
    database: str | None,
) -> list[dict[str, str]]:
    label_col = label_column or value_column
    with connection(database) as conn:
        _ensure_vendor_info_manual_column(conn)
        columns = {c.name for c in ensure_table_exists(conn, "vendor_info")}
        if value_column not in columns:
            raise ValueError(f"Column not found: {value_column}")
        if label_col not in columns:
            raise ValueError(f"Column not found: {label_col}")

        has_vendor_code = "vendor_code" in columns
        has_vendor_name = "vendor_name" in columns
        value_expr = quote_ident(value_column)
        label_expr = quote_ident(label_col)
        if has_vendor_name and has_vendor_code and value_column == "vendor_code":
            value_expr = (
                f"COALESCE(NULLIF(TRIM({quote_ident('vendor_name')}), ''), "
                f"NULLIF(TRIM({quote_ident('vendor_code')}), ''))"
            )
        if has_vendor_name and has_vendor_code and label_col == "vendor_code":
            label_expr = (
                f"COALESCE(NULLIF(TRIM({quote_ident('vendor_name')}), ''), "
                f"NULLIF(TRIM({quote_ident('vendor_code')}), ''))"
            )

        query = (
            f"SELECT DISTINCT {value_expr} AS value, {label_expr} AS label, "
            f"COALESCE(is_manual, 0) AS is_manual "
            f"FROM {quote_ident('vendor_info')} "
            f"WHERE {value_expr} IS NOT NULL AND TRIM({value_expr}) != '' "
            f"ORDER BY COALESCE(is_manual, 0) DESC, {label_expr}"
        )
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    return [
        {
            "value": str(row["value"]),
            "label": str(row["label"]),
            "is_manual": str(int(row.get("is_manual") or 0)),
        }
        for row in rows
    ]


def quote_ident(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def get_table_ddl(table_name: str, database: str | None = None) -> str:
    with connection(database) as conn:
        ensure_table_exists(conn, table_name)
        with conn.cursor() as cursor:
            cursor.execute(f"SHOW CREATE TABLE {quote_ident(table_name)}")
            row = cursor.fetchone()
    return str(row[1])


def execute_ddl(sql: str, database: str | None = None) -> None:
    with connection(database) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
        conn.commit()
