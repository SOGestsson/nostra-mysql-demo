from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterator

import mysql.connector
from mysql.connector import MySQLConnection


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


def list_rows(table_name: str, limit: int, offset: int, database: str | None = None, stock_out: bool = False) -> list[dict[str, Any]]:
    with connection(database) as conn:
        ensure_table_exists(conn, table_name)
        if table_name == 'items':
            _ensure_vendor_overrides_table(conn)
            join = (
                "LEFT JOIN vendor_overrides vo "
                "ON items.id = vo.item_id AND vo.set_at > DATE_SUB(NOW(), INTERVAL 30 DAY)"
            )
            select = (
                "SELECT items.*, "
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
