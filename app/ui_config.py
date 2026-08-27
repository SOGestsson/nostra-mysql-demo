from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app import db
from app.sql_filter import migrate_table_sql_filters, normalize_sql_filter

HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
GRID_PAGING_MODES = frozenset({"pages", "scroll"})
MAX_SAVED_WHERE_FILTERS = 50
MAX_WHERE_EXPRESSION_LEN = 4000
MAX_WHERE_FILTER_NAME_LEN = 120
MIN_COLUMN_WIDTH = 40
MAX_COLUMN_WIDTH = 1200
MAX_FROZEN_COLUMNS = 100


class NativeEditorConfig(BaseModel):
    type: Literal["native"] = "native"
    table: str | None = None
    column: str | None = None


class EnumEditorConfig(BaseModel):
    type: Literal["enum"] = "enum"
    options: list[str]


class LookupEditorConfig(BaseModel):
    type: Literal["lookup"] = "lookup"
    table: str
    valueColumn: str
    labelColumn: str | None = None
    save: Literal["direct", "override"] = "direct"


class UsersEditorConfig(BaseModel):
    type: Literal["users"] = "users"
    table: str = "order_lines"
    column: str = "assigned_to"


ColumnEditorConfig = NativeEditorConfig | EnumEditorConfig | LookupEditorConfig | UsersEditorConfig


class SavedWhereFilter(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=MAX_WHERE_FILTER_NAME_LEN)
    expression: str = Field(min_length=1, max_length=MAX_WHERE_EXPRESSION_LEN)
    enabled: bool = True

    @field_validator("id", "name", "expression", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()

    @field_validator("name", "expression")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be empty")
        return value


class DbUiConfigPayload(BaseModel):
    editableColumns: list[str] = []
    visibleColumns: list[str] = []
    filterableColumns: list[str] = []
    hiddenColumns: list[str] = []
    visiblePages: list[str] = []
    columnEditors: dict[str, dict[str, Any]] = Field(default_factory=dict)
    columnLabels: dict[str, str] = Field(default_factory=dict)
    catalogTable: str = "items"
    vendorOverrideDays: int = Field(default=30, ge=0, le=3650)
    progressStatusColors: dict[str, str] = Field(default_factory=dict)
    catalogSharedWhereFilters: list[SavedWhereFilter] = Field(default_factory=list)
    ordersSharedWhereFilters: list[SavedWhereFilter] = Field(default_factory=list)
    optimalPlanSharedWhereFilters: list[SavedWhereFilter] = Field(default_factory=list)
    catalogSqlFilter: str = ""
    ordersSqlFilter: str = ""
    optimalPlanSqlFilter: str = ""
    tableSqlFilters: dict[str, str] = Field(default_factory=dict)


def _normalize_hex_color(value: str) -> str:
    color = str(value).strip()
    if not HEX_COLOR_RE.match(color):
        raise ValueError(f"Invalid hex color '{color}'. Use values like #ff8800.")
    if len(color) == 4:
        return "#" + "".join(ch * 2 for ch in color[1:])
    return color.lower()


def _validate_progress_status_colors(raw: dict[str, str]) -> dict[str, str]:
    validated: dict[str, str] = {}
    for key, value in (raw or {}).items():
        status = str(key).strip()
        if not status:
            continue
        validated[status] = _normalize_hex_color(value)
    return validated


def validate_progress_status_colors(raw: dict[str, str]) -> dict[str, str]:
    return _validate_progress_status_colors(raw)


def _parse_editor(raw: dict[str, Any]) -> ColumnEditorConfig:
    editor_type = raw.get("type", "native")
    if editor_type == "enum":
        return EnumEditorConfig(**raw)
    if editor_type == "lookup":
        return LookupEditorConfig(**raw)
    if editor_type == "users":
        return UsersEditorConfig(**raw)
    return NativeEditorConfig(**raw)


def validate_db_ui_config(config: dict[str, Any], database: str) -> dict[str, Any]:
    payload = DbUiConfigPayload.model_validate(config)
    catalog_table = payload.catalogTable

    with db.connection(database) as conn:
        columns = db.get_columns(conn, catalog_table)
    column_names = {c.name for c in columns}

    for col in payload.editableColumns:
        editor_raw = payload.columnEditors.get(col, {})
        editor_table = editor_raw.get("table")
        editor_column = editor_raw.get("column")
        if col not in column_names and not (editor_table and editor_column):
            raise ValueError(f"Editable column '{col}' not found in table '{catalog_table}'")

    validated_editors: dict[str, dict[str, Any]] = {}
    for col, raw in payload.columnEditors.items():
        if col not in payload.editableColumns:
            raise ValueError(f"columnEditors key '{col}' must also be listed in editableColumns")
        editor = _parse_editor(raw)
        if isinstance(editor, NativeEditorConfig):
            if editor.table or editor.column:
                table_name = editor.table or catalog_table
                column_name = editor.column or col
                with db.connection(database) as conn:
                    native_columns = {c.name for c in db.get_columns(conn, table_name)}
                if column_name not in native_columns:
                    raise ValueError(
                        f"native editor for '{col}': column '{column_name}' "
                        f"not found in table '{table_name}'"
                    )
        elif isinstance(editor, EnumEditorConfig):
            if not editor.options:
                raise ValueError(f"enum editor for '{col}' requires non-empty options")
        elif isinstance(editor, LookupEditorConfig):
            with db.connection(database) as conn:
                lookup_columns = {c.name for c in db.get_columns(conn, editor.table)}
            for field_name, field_value in (
                ("valueColumn", editor.valueColumn),
                ("labelColumn", editor.labelColumn or editor.valueColumn),
            ):
                if field_value not in lookup_columns:
                    raise ValueError(
                        f"lookup editor for '{col}': {field_name} '{field_value}' "
                        f"not found in table '{editor.table}'"
                    )
        elif isinstance(editor, UsersEditorConfig):
            with db.connection(database) as conn:
                db.ensure_table_exists(conn, editor.table)
                user_columns = {c.name for c in db.get_columns(conn, editor.table)}
            if editor.column not in user_columns:
                raise ValueError(
                    f"users editor for '{col}': column '{editor.column}' "
                    f"not found in table '{editor.table}'"
                )
        validated_editors[col] = editor.model_dump()

    result = payload.model_dump()
    result["columnEditors"] = validated_editors
    result["columnLabels"] = {
        key: value.strip()
        for key, value in payload.columnLabels.items()
        if value and value.strip()
    }
    result["progressStatusColors"] = _validate_progress_status_colors(payload.progressStatusColors)
    result["catalogSharedWhereFilters"] = validate_saved_where_filters(payload.catalogSharedWhereFilters)
    result["ordersSharedWhereFilters"] = validate_saved_where_filters(payload.ordersSharedWhereFilters)
    result["optimalPlanSharedWhereFilters"] = validate_saved_where_filters(
        payload.optimalPlanSharedWhereFilters,
    )
    result["catalogSqlFilter"] = normalize_sql_filter(payload.catalogSqlFilter)
    result["ordersSqlFilter"] = normalize_sql_filter(payload.ordersSqlFilter)
    result["optimalPlanSqlFilter"] = normalize_sql_filter(payload.optimalPlanSqlFilter)
    table_filters = migrate_table_sql_filters(result)
    result["tableSqlFilters"] = table_filters
    catalog_table = str(result.get("catalogTable") or "items").strip() or "items"
    result["catalogSqlFilter"] = table_filters.get(catalog_table, "")
    result["ordersSqlFilter"] = table_filters.get("order_lines", "")
    return result


def _new_saved_where_filter_id() -> str:
    return str(uuid.uuid4())


def validate_saved_where_filters(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("savedWhereFilters must be a list")
    if len(raw) > MAX_SAVED_WHERE_FILTERS:
        raise ValueError(f"At most {MAX_SAVED_WHERE_FILTERS} saved WHERE filters are allowed")

    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        data = dict(item)
        filter_id = str(data.get("id") or "").strip() or _new_saved_where_filter_id()
        if filter_id in seen_ids:
            filter_id = _new_saved_where_filter_id()
        seen_ids.add(filter_id)
        data["id"] = filter_id
        validated.append(SavedWhereFilter.model_validate(data).model_dump())

    return validated


def _validate_string_list(raw: Any, field_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be a list")
    result: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value:
            result.append(value)
    return result


def _validate_column_widths(raw: Any, field_name: str) -> dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be an object")
    result: dict[str, int] = {}
    for key, value in raw.items():
        column_key = str(key).strip()
        if not column_key:
            continue
        try:
            width = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name}.{column_key} must be an integer") from exc
        if width < MIN_COLUMN_WIDTH or width > MAX_COLUMN_WIDTH:
            raise ValueError(
                f"{field_name}.{column_key} must be between {MIN_COLUMN_WIDTH} and {MAX_COLUMN_WIDTH}"
            )
        result[column_key] = width
    return result


def _validate_frozen_count(raw: Any, field_name: str) -> int:
    try:
        count = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if count < 0 or count > MAX_FROZEN_COLUMNS:
        raise ValueError(f"{field_name} must be between 0 and {MAX_FROZEN_COLUMNS}")
    return count


def _validate_grid_paging(raw: Any, field_name: str) -> str:
    mode = str(raw or "").strip().lower()
    if mode not in GRID_PAGING_MODES:
        raise ValueError(f"{field_name} must be one of: {', '.join(sorted(GRID_PAGING_MODES))}")
    return mode


USER_DB_CONFIG_FIELDS: dict[str, str] = {
    "visibleColumns": "string_list",
    "filterableColumns": "string_list",
    "ordersVisibleColumns": "string_list",
    "ordersFilterableColumns": "string_list",
    "columnWidths": "column_widths",
    "ordersColumnWidths": "column_widths",
    "catalogFrozenColumnCount": "frozen_count",
    "ordersFrozenColumnCount": "frozen_count",
    "catalogShowRowNumbers": "bool",
    "ordersShowRowNumbers": "bool",
    "catalogSavedWhereFilters": "saved_where_filters",
    "ordersSavedWhereFilters": "saved_where_filters",
    "catalogGridPaging": "grid_paging",
    "ordersGridPaging": "grid_paging",
    "optimalPlanVisibleColumns": "string_list",
    "optimalPlanFilterableColumns": "string_list",
    "optimalPlanColumnWidths": "column_widths",
    "optimalPlanFrozenColumnCount": "frozen_count",
    "optimalPlanShowRowNumbers": "bool",
    "optimalPlanGridPaging": "grid_paging",
    "optimalPlanSavedWhereFilters": "saved_where_filters",
}


def validate_user_db_config_updates(updates: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for key, value in updates.items():
        field_type = USER_DB_CONFIG_FIELDS.get(key)
        if field_type is None:
            raise ValueError(f"Unknown user db-config field '{key}'")
        if field_type == "string_list":
            validated[key] = _validate_string_list(value, key)
        elif field_type == "column_widths":
            validated[key] = _validate_column_widths(value, key)
        elif field_type == "frozen_count":
            validated[key] = _validate_frozen_count(value, key)
        elif field_type == "bool":
            validated[key] = bool(value)
        elif field_type == "saved_where_filters":
            validated[key] = validate_saved_where_filters(value)
        elif field_type == "grid_paging":
            validated[key] = _validate_grid_paging(value, key)
    return validated


def normalize_user_db_config(config: dict[str, Any]) -> dict[str, Any]:
    if not config:
        return {}
    result = dict(config)
    for key, field_type in USER_DB_CONFIG_FIELDS.items():
        if key not in result:
            continue
        value = result[key]
        try:
            if field_type == "string_list":
                result[key] = _validate_string_list(value, key)
            elif field_type == "column_widths":
                result[key] = _validate_column_widths(value, key)
            elif field_type == "frozen_count":
                result[key] = _validate_frozen_count(value, key)
            elif field_type == "bool":
                result[key] = bool(value)
            elif field_type == "saved_where_filters":
                result[key] = validate_saved_where_filters(value)
            elif field_type == "grid_paging":
                result[key] = _validate_grid_paging(value, key)
        except ValueError:
            if field_type == "saved_where_filters":
                result[key] = []
            elif field_type == "string_list":
                result[key] = []
            elif field_type == "column_widths":
                result[key] = {}
            elif field_type == "frozen_count":
                result[key] = 0
            elif field_type == "bool":
                result[key] = False
            elif field_type == "grid_paging":
                result[key] = "pages"
    return result
