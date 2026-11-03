"""New M7 tables. These are created only on a new customer database.

The statements never alter items, item_histories, or any other existing table.
drilling, smart_stock, and consumables are refused before any connection is used
to create tables.
"""

from __future__ import annotations

# demo is the registered name of the smart_stock database.
LIVE_PURCHASING_DATABASES = frozenset({"drilling", "smart_stock", "consumables", "demo"})


class LiveDatabaseRefused(Exception):
    """Raised when a caller asks to prepare M7 tables on a live purchasing database."""


def assert_m7_database_allowed(name: str) -> str:
    logical = (name or "").strip()
    if not logical:
        raise ValueError("Database name is required")
    if logical.casefold() in LIVE_PURCHASING_DATABASES:
        raise LiveDatabaseRefused(
            f"{logical} is a live purchasing database. M7 tables are not created there."
        )
    return logical


CREATE_TABLES: dict[str, str] = {
    "m7_settings": """
        CREATE TABLE IF NOT EXISTS m7_settings (
            setting_key VARCHAR(64) NOT NULL PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
    """,
    "m7_forecast_class": """
        CREATE TABLE IF NOT EXISTS m7_forecast_class (
            code VARCHAR(16) NOT NULL PRIMARY KEY,
            label VARCHAR(64) NOT NULL,
            handling VARCHAR(16) NOT NULL,
            history_for_links TINYINT NOT NULL DEFAULT 1,
            in_export TINYINT NOT NULL DEFAULT 1,
            use_safety_stock TINYINT NOT NULL DEFAULT 0,
            note VARCHAR(255) NOT NULL DEFAULT ''
        )
    """,
    "m7_item_handling": """
        CREATE TABLE IF NOT EXISTS m7_item_handling (
            item_number VARCHAR(64) NOT NULL PRIMARY KEY,
            handling_override VARCHAR(16) NULL,
            easter TINYINT NOT NULL DEFAULT 0
        )
    """,
    "m7_item_class": """
        CREATE TABLE IF NOT EXISTS m7_item_class (
            item_number VARCHAR(64) NOT NULL PRIMARY KEY,
            class_code VARCHAR(16) NOT NULL
        )
    """,
    "m7_abc_class": """
        CREATE TABLE IF NOT EXISTS m7_abc_class (
            code VARCHAR(1) NOT NULL PRIMARY KEY,
            use_safety_stock TINYINT NOT NULL DEFAULT 0
        )
    """,
    "m7_item_revenue_abc": """
        CREATE TABLE IF NOT EXISTS m7_item_revenue_abc (
            item_number VARCHAR(64) NOT NULL PRIMARY KEY,
            abc_code VARCHAR(1) NOT NULL
        )
    """,
    "m7_item_safety": """
        CREATE TABLE IF NOT EXISTS m7_item_safety (
            item_number VARCHAR(64) NOT NULL PRIMARY KEY,
            mode VARCHAR(16) NOT NULL DEFAULT '',
            service_level DECIMAL(8, 4) NULL,
            qty_override DECIMAL(18, 4) NULL
        )
    """,
    "m7_item_link": """
        CREATE TABLE IF NOT EXISTS m7_item_link (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            receiving_item_number VARCHAR(64) NOT NULL,
            source_item_number VARCHAR(64) NOT NULL,
            factor DECIMAL(18, 4) NOT NULL,
            active TINYINT NOT NULL DEFAULT 1,
            to_date DATE NULL,
            KEY idx_m7_item_link_receiving (receiving_item_number),
            KEY idx_m7_item_link_source (source_item_number)
        )
    """,
    "m7_market_forecast": """
        CREATE TABLE IF NOT EXISTS m7_market_forecast (
            item_number VARCHAR(64) NOT NULL,
            forecast_year SMALLINT NOT NULL,
            forecast_month TINYINT NOT NULL,
            qty DECIMAL(18, 4) NOT NULL,
            PRIMARY KEY (item_number, forecast_year, forecast_month)
        )
    """,
    "m7_production_usage": """
        CREATE TABLE IF NOT EXISTS m7_production_usage (
            item_number VARCHAR(64) NOT NULL,
            usage_year SMALLINT NOT NULL,
            usage_month TINYINT NOT NULL,
            qty DECIMAL(18, 4) NOT NULL,
            PRIMARY KEY (item_number, usage_year, usage_month)
        )
    """,
    "m7_forecast": """
        CREATE TABLE IF NOT EXISTS m7_forecast (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            item_number VARCHAR(64) NOT NULL,
            forecast_year SMALLINT NOT NULL,
            forecast_month TINYINT NOT NULL,
            qty DECIMAL(18, 4) NOT NULL,
            model VARCHAR(64) NULL,
            run_id VARCHAR(64) NULL,
            UNIQUE KEY uq_m7_forecast_item_month (item_number, forecast_year, forecast_month)
        )
    """,
    "m7_forecast_override": """
        CREATE TABLE IF NOT EXISTS m7_forecast_override (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            item_number VARCHAR(64) NOT NULL,
            forecast_year SMALLINT NOT NULL,
            forecast_month TINYINT NOT NULL,
            qty DECIMAL(18, 4) NOT NULL,
            UNIQUE KEY uq_m7_forecast_override_item_month (item_number, forecast_year, forecast_month)
        )
    """,
    "m7_forecast_run": """
        CREATE TABLE IF NOT EXISTS m7_forecast_run (
            id VARCHAR(64) NOT NULL PRIMARY KEY,
            started_at DATETIME NOT NULL,
            finished_at DATETIME NULL,
            status VARCHAR(16) NOT NULL,
            model VARCHAR(64) NULL,
            scope VARCHAR(64) NULL,
            item_count INT NOT NULL DEFAULT 0,
            message TEXT NOT NULL
        )
    """,
    "m7_sync_log": """
        CREATE TABLE IF NOT EXISTS m7_sync_log (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            started_at DATETIME NOT NULL,
            status VARCHAR(16) NOT NULL,
            message TEXT NOT NULL
        )
    """,
}


def ensure_forecast_class_safety_stock(conn) -> str:
    """Add the class flag that marks items whose safety stock is exported."""
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM m7_forecast_class LIKE 'use_safety_stock'")
        if cursor.fetchone():
            return "already present"
        cursor.execute(
            "ALTER TABLE m7_forecast_class ADD COLUMN use_safety_stock TINYINT NOT NULL DEFAULT 0"
        )
    conn.commit()
    return "added"


def ensure_abc_classes(conn) -> None:
    """Seed the revenue ABC letters. Safety stock is decided here, not on the forecast class."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT IGNORE INTO m7_abc_class (code, use_safety_stock) VALUES (%s, 0)",
            [("A",), ("B",), ("C",)],
        )
    conn.commit()


def ensure_item_handling_easter(conn) -> str:
    """Mark an item as páska so its forecast uses the Easter month placement."""
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM m7_item_handling LIKE 'easter'")
        if cursor.fetchone():
            return "already present"
        cursor.execute(
            "ALTER TABLE m7_item_handling ADD COLUMN easter TINYINT NOT NULL DEFAULT 0"
        )
    conn.commit()
    return "added"


def ensure_item_link_to_date(conn) -> str:
    """Add to_date on an existing m7_item_link. New databases get it from CREATE TABLE."""
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM m7_item_link LIKE 'to_date'")
        if cursor.fetchone():
            return "already present"
        cursor.execute("ALTER TABLE m7_item_link ADD COLUMN to_date DATE NULL")
    conn.commit()
    return "added"


def create_m7_tables(conn) -> dict[str, str]:
    """Create the new tables on an already-checked customer connection."""
    states: dict[str, str] = {}
    with conn.cursor() as cursor:
        for name, statement in CREATE_TABLES.items():
            cursor.execute("SHOW TABLES LIKE %s", (name,))
            states[name] = "already present" if cursor.fetchone() else "created"
            cursor.execute(statement)
    conn.commit()
    ensure_item_link_to_date(conn)
    ensure_item_handling_easter(conn)
    ensure_forecast_class_safety_stock(conn)
    ensure_abc_classes(conn)
    return states


def normalize_m7_forecast_rows(rows: list[dict]) -> tuple[list[tuple], list[str]]:
    """Validate forecast month rows for one bulk replace.

    Returns (insert tuples, distinct item numbers to clear first).
    """
    prepared: list[tuple] = []
    items: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            raise ValueError("Each forecast row must be an object")
        item_number = str(row.get("item_number") or "").strip()
        if not item_number:
            raise ValueError("item_number is required")
        try:
            year = int(row.get("forecast_year"))
            month = int(row.get("forecast_month"))
            qty = float(row.get("qty"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Bad forecast row for {item_number}: {exc}") from exc
        if month < 1 or month > 12:
            raise ValueError(f"Bad forecast month {month} for {item_number}")
        model = row.get("model")
        run_id = row.get("run_id")
        prepared.append(
            (
                item_number,
                year,
                month,
                qty,
                None if model is None else str(model)[:64],
                None if run_id is None else str(run_id)[:64],
            )
        )
        if item_number not in seen:
            seen.add(item_number)
            items.append(item_number)
    return prepared, items


def replace_m7_forecast(conn, rows: list[dict]) -> dict[str, int]:
    """Delete existing months for the given items, then insert the new rows.

    One transaction. Items with no successful months must not be passed in;
    their previous forecast stays.
    """
    prepared, items = normalize_m7_forecast_rows(rows)
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE %s", ("m7_forecast",))
        if not cursor.fetchone():
            raise ValueError("m7_forecast is missing")
        deleted = 0
        if items:
            for start in range(0, len(items), 500):
                chunk = items[start : start + 500]
                placeholders = ", ".join(["%s"] * len(chunk))
                cursor.execute(
                    f"DELETE FROM m7_forecast WHERE item_number IN ({placeholders})",
                    chunk,
                )
                deleted += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        if prepared:
            cursor.executemany(
                """
                INSERT INTO m7_forecast
                    (item_number, forecast_year, forecast_month, qty, model, run_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                prepared,
            )
    conn.commit()
    return {
        "items": len(items),
        "deleted_rows": deleted,
        "inserted_rows": len(prepared),
    }
