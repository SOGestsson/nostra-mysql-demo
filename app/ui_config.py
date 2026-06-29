from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app import db


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


ColumnEditorConfig = NativeEditorConfig | EnumEditorConfig | LookupEditorConfig


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


def _parse_editor(raw: dict[str, Any]) -> ColumnEditorConfig:
    editor_type = raw.get("type", "native")
    if editor_type == "enum":
        return EnumEditorConfig(**raw)
    if editor_type == "lookup":
        return LookupEditorConfig(**raw)
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
        validated_editors[col] = editor.model_dump()

    result = payload.model_dump()
    result["columnEditors"] = validated_editors
    result["columnLabels"] = {
        key: value.strip()
        for key, value in payload.columnLabels.items()
        if value and value.strip()
    }
    return result
