from __future__ import annotations

import re
from typing import Any

SQL_FILTER_MAX_LEN = 4000

_SQL_FILTER_FORBIDDEN = re.compile(
    r"""
    (?:--|\#|/\*|\*/|;)
    | \b(?:UNION|SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|
           GRANT|REVOKE|CALL|PROCEDURE|FUNCTION|TRIGGER|EVENT|EXECUTE|
           INTO|OUTFILE|DUMPFILE|LOAD_FILE|SLEEP|BENCHMARK|
           FROM|JOIN|GROUP|HAVING|ORDER|
           INFORMATION_SCHEMA|PERFORMANCE_SCHEMA)\b
    | \b(?:mysql|information_schema|performance_schema|sys|nostradamus_master)\s*\.
    | `(?:mysql|information_schema|performance_schema|sys|nostradamus_master)`\s*\.
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_sql_filter(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if len(text) > SQL_FILTER_MAX_LEN:
        raise ValueError(f"SQL filter must be at most {SQL_FILTER_MAX_LEN} characters")
    lowered = text.lower()
    if lowered.startswith("where "):
        text = text[6:].strip()
    elif lowered.startswith("and "):
        text = text[4:].strip()
    if not text:
        return ""
    if _SQL_FILTER_FORBIDDEN.search(text):
        raise ValueError("SQL filter contains disallowed syntax")
    if "%s" in text.lower() or "%(" in text:
        raise ValueError("SQL filter contains disallowed syntax")
    return text


def sql_filters_from_list(raw: Any) -> str:
    if not isinstance(raw, list):
        return ""
    parts: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        expression = normalize_sql_filter(item.get("expression"))
        if expression:
            parts.append(f"({expression})")
    return " AND ".join(parts)


def resolve_sql_filter(config: dict[str, Any] | None, sql_key: str, list_key: str) -> str:
    config = config or {}
    direct = normalize_sql_filter(config.get(sql_key))
    if direct:
        return direct
    return sql_filters_from_list(config.get(list_key))


def combine_sql_filters(*fragments: str) -> str:
    parts = [f"({text})" for text in fragments if text]
    return " AND ".join(parts)


def append_sql_filter(where_clause: str, fragment: str) -> str:
    fragment = (fragment or "").strip()
    if not fragment:
        return where_clause or ""
    extra = f"({fragment})"
    base = (where_clause or "").strip()
    if not base:
        return f"WHERE {extra}"
    return f"{base} AND {extra}"


_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_table_sql_filters(raw: Any) -> dict[str, str]:
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("tableSqlFilters must be an object of table → SQL")
    out: dict[str, str] = {}
    for key, value in raw.items():
        table = str(key or "").strip()
        if not table:
            continue
        if not _TABLE_NAME_RE.match(table):
            raise ValueError(f"Invalid table name in SQL filters: '{table}'")
        expression = normalize_sql_filter(value)
        if expression:
            out[table] = expression
    return out


def migrate_table_sql_filters(config: dict[str, Any] | None) -> dict[str, str]:
    config = config or {}
    filters = normalize_table_sql_filters(config.get("tableSqlFilters"))
    catalog_table = str(config.get("catalogTable") or "items").strip() or "items"
    if catalog_table not in filters:
        legacy = resolve_sql_filter(config, "catalogSqlFilter", "catalogSharedWhereFilters")
        if not legacy:
            legacy = resolve_sql_filter(config, "optimalPlanSqlFilter", "optimalPlanSharedWhereFilters")
        if legacy:
            filters[catalog_table] = legacy
    if "order_lines" not in filters:
        legacy = resolve_sql_filter(config, "ordersSqlFilter", "ordersSharedWhereFilters")
        if legacy:
            filters["order_lines"] = legacy
    return filters
