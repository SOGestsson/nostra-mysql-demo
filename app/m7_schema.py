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
            note VARCHAR(255) NOT NULL DEFAULT ''
        )
    """,
    "m7_item_handling": """
        CREATE TABLE IF NOT EXISTS m7_item_handling (
            item_number VARCHAR(64) NOT NULL PRIMARY KEY,
            handling_override VARCHAR(16) NULL
        )
    """,
    "m7_item_class": """
        CREATE TABLE IF NOT EXISTS m7_item_class (
            item_number VARCHAR(64) NOT NULL PRIMARY KEY,
            class_code VARCHAR(16) NOT NULL
        )
    """,
    "m7_item_link": """
        CREATE TABLE IF NOT EXISTS m7_item_link (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            receiving_item_number VARCHAR(64) NOT NULL,
            source_item_number VARCHAR(64) NOT NULL,
            factor DECIMAL(18, 4) NOT NULL,
            active TINYINT NOT NULL DEFAULT 1,
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
    "m7_forecast": """
        CREATE TABLE IF NOT EXISTS m7_forecast (
            item_number VARCHAR(64) NOT NULL,
            forecast_year SMALLINT NOT NULL,
            forecast_month TINYINT NOT NULL,
            qty DECIMAL(18, 4) NOT NULL,
            model VARCHAR(64) NULL,
            run_id VARCHAR(64) NULL,
            PRIMARY KEY (item_number, forecast_year, forecast_month)
        )
    """,
    "m7_forecast_override": """
        CREATE TABLE IF NOT EXISTS m7_forecast_override (
            item_number VARCHAR(64) NOT NULL,
            forecast_year SMALLINT NOT NULL,
            forecast_month TINYINT NOT NULL,
            qty DECIMAL(18, 4) NOT NULL,
            PRIMARY KEY (item_number, forecast_year, forecast_month)
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


def create_m7_tables(conn) -> dict[str, str]:
    """Create the new tables on an already-checked customer connection."""
    states: dict[str, str] = {}
    with conn.cursor() as cursor:
        for name, statement in CREATE_TABLES.items():
            cursor.execute("SHOW TABLES LIKE %s", (name,))
            states[name] = "already present" if cursor.fetchone() else "created"
            cursor.execute(statement)
    conn.commit()
    return states
