from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
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
            vendor_override_days = _get_vendor_override_days(database)
            join = (
                "LEFT JOIN vendor_overrides vo "
                f"ON items.id = vo.item_id AND vo.set_at > DATE_SUB(NOW(), INTERVAL {vendor_override_days} DAY)"
            )
            select = (
                "SELECT items.*, "
                "items.vendor_name AS item_vendor_name, "
                "COALESCE(vo.vendor_name, items.vendor_name) AS vendor_name, "
                "vo.set_at AS vendor_override_set_at "
                f"FROM items {join}"
            )
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
        item = _get_single_row(
            conn,
            "SELECT * FROM `items` WHERE `id` = %s",
            (item_id,),
        )
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


def reset_purchase_suggestions(database: str | None = None) -> int:
    with connection(database) as conn:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE items SET purchase_suggestion = 0")
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


def create_order_from_purchase_suggestions(
    database: str | None = None,
    user_id: int | None = None,
    description: str | None = None,
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
                """,
                (order_id,),
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
            """
            SELECT
                orders.*,
                COUNT(order_lines.id) AS line_count,
                COALESCE(SUM(COALESCE(order_lines.qty_override, order_lines.qty_suggested)), 0) AS total_qty
            FROM orders
            LEFT JOIN order_lines
                ON orders.id = order_lines.order_id
                AND COALESCE(order_lines.deleted, 0) = 0
            GROUP BY orders.id
            ORDER BY COALESCE(orders.created_at, orders.order_date) DESC, orders.id DESC
            LIMIT %s
            """,
            (limit,),
        )


def _order_items_select_sql(vendor_override_days: int) -> str:
    return f"""
            SELECT
                items.*,
                items.vendor_name AS item_vendor_name,
                COALESCE(vo.vendor_name, items.vendor_name) AS vendor_name,
                vo.set_at AS vendor_override_set_at,
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
                order_lines.progress AS order_line_progress
            FROM {{from_clause}}
            LEFT JOIN vendor_overrides vo
                ON items.id = vo.item_id
                AND vo.set_at > DATE_SUB(NOW(), INTERVAL {vendor_override_days} DAY)
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
        vendor_override_days = _get_vendor_override_days(database)
        select_sql = _order_items_select_sql(vendor_override_days)
        if order_lines_only:
            query = select_sql.format(
                from_clause="""
            order_lines
            INNER JOIN orders ON orders.id = order_lines.order_id
            INNER JOIN items ON items.id = order_lines.item_id""",
                where_clause="WHERE order_lines.order_id = %s AND COALESCE(order_lines.deleted, 0) = 0",
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


def upsert_sim_result(rows: list[dict[str, Any]], database: str | None = None) -> int:
    if not rows:
        return 0

    item_ids = list({row["item_id"] for row in rows})

    with connection(database) as conn:
        with conn.cursor() as cursor:
            fmt = ",".join(["%s"] * len(item_ids))
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
        conn.commit()
        return len(rows)


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
