from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

import mysql.connector
from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import db, auth as auth_module, ui_config as ui_config_module, assistant as assistant_module

logger = logging.getLogger(__name__)


class PurchaseSuggestion(BaseModel):
    item_id: int
    purchase_qty: float | None = None
    current_datetime: str | None = None


class SimResultRow(BaseModel):
    item_id: int
    inv: float | None = None
    purchase_qty: float | None = None
    deliveries: float | None = None
    lost_sale: float | None = None
    expired: float | None = None
    sim_date: str | None = None
    forecast: float | None = None
    actual_sale: float | None = None


class MultiSimResult(BaseModel):
    sim_result: list[SimResultRow]
    purchase_suggestions: list[Any] = []


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    database_name: str
    is_admin: bool = False


class LoginRequest(BaseModel):
    email: str
    password: str


app = FastAPI(title="Nostra MySQL CRUD API")

class VendorOverridePayload(BaseModel):
    vendor_name: str


class PurchasingMethodOverridePayload(BaseModel):
    purchasing_method: str


class CreateOrderFromSuggestionsPayload(BaseModel):
    description: str | None = None
    source_order_id: int | None = None
    item_ids: list[int] | None = None


class AddOrderLinePayload(BaseModel):
    item_number: str
    qty: int
    description: str | None = None
    vendor_name: str | None = None
    unit_price: float | None = None


class MergeOrderLinesPayload(BaseModel):
    source_order_id: int
    progress_statuses: list[str] | None = None
    set_progress: str | None = None


class ResetPurchaseSuggestionsPayload(BaseModel):
    item_ids: list[int] | None = None
    source_order_id: int | None = None


class SimOptimalPlanTimeseriesPayload(BaseModel):
    item_ids: list[int] | None = None


class UserDbConfigPayload(BaseModel):
    visibleColumns: list[str] | None = None
    filterableColumns: list[str] | None = None
    ordersVisibleColumns: list[str] | None = None
    ordersFilterableColumns: list[str] | None = None
    columnWidths: dict[str, int] | None = None
    ordersColumnWidths: dict[str, int] | None = None
    catalogFrozenColumnCount: int | None = None
    ordersFrozenColumnCount: int | None = None
    catalogShowRowNumbers: bool | None = None
    ordersShowRowNumbers: bool | None = None
    catalogSavedWhereFilters: list[ui_config_module.SavedWhereFilter] | None = None
    ordersSavedWhereFilters: list[ui_config_module.SavedWhereFilter] | None = None
    catalogGridPaging: str | None = None
    ordersGridPaging: str | None = None
    optimalPlanVisibleColumns: list[str] | None = None
    optimalPlanFilterableColumns: list[str] | None = None
    optimalPlanColumnWidths: dict[str, int] | None = None
    optimalPlanFrozenColumnCount: int | None = None
    optimalPlanShowRowNumbers: bool | None = None
    optimalPlanGridPaging: str | None = None
    optimalPlanSavedWhereFilters: list[ui_config_module.SavedWhereFilter] | None = None


class ProgressStatusColorsPayload(BaseModel):
    progressStatusColors: dict[str, str] = Field(default_factory=dict)


class ManualVendorPayload(BaseModel):
    vendor_name: str


class ReplaceSyncedVendorsPayload(BaseModel):
    vendor_names: list[str] = []


@app.on_event("startup")
def startup() -> None:
    auth_module.ensure_users_table()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> dict:
    try:
        user = auth_module.register_user(
            username=payload.username,
            email=payload.email,
            password=payload.password,
            database_name=payload.database_name,
            is_admin=payload.is_admin,
        )
        return {"user": user}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/admin/users")
def admin_list_users(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.require_admin(token)
        return {"users": auth_module.list_users()}
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/db-users")
def list_db_users(
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.require_db_users_access(token, db_name)
        return {"users": auth_module.list_users_for_database(db_name)}
    except ValueError as exc:
        message = str(exc)
        status_code = 401 if message in {"Token expired", "Invalid token"} else 403
        raise HTTPException(status_code=status_code, detail=message) from exc


@app.post("/admin/users", status_code=status.HTTP_201_CREATED)
def admin_create_user(payload: RegisterRequest, authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.require_admin(token)
        user = auth_module.register_user(
            username=payload.username,
            email=payload.email,
            password=payload.password,
            database_name=payload.database_name,
            is_admin=payload.is_admin,
        )
        return {"user": user}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/db-config/{db_name}")
def get_db_config(db_name: str, authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        return {"db_name": db_name, "config": auth_module.get_db_ui_config(db_name)}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/user/db-config/{db_name}")
def get_user_db_config(db_name: str, authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        user = auth_module.verify_token(token)
        admin_config = auth_module.get_db_ui_config(db_name)
        user_config = ui_config_module.normalize_user_db_config(
            auth_module.get_user_ui_config(user["id"], db_name),
        )
        merged = {
            **admin_config,
            **({"visibleColumns": user_config["visibleColumns"]} if "visibleColumns" in user_config else {}),
            **({"filterableColumns": user_config["filterableColumns"]} if "filterableColumns" in user_config else {}),
            **({"ordersVisibleColumns": user_config["ordersVisibleColumns"]} if "ordersVisibleColumns" in user_config else {}),
            **({"ordersFilterableColumns": user_config["ordersFilterableColumns"]} if "ordersFilterableColumns" in user_config else {}),
            **({"columnWidths": user_config["columnWidths"]} if "columnWidths" in user_config else {}),
            **({"ordersColumnWidths": user_config["ordersColumnWidths"]} if "ordersColumnWidths" in user_config else {}),
            **({"catalogFrozenColumnCount": user_config["catalogFrozenColumnCount"]} if "catalogFrozenColumnCount" in user_config else {}),
            **({"ordersFrozenColumnCount": user_config["ordersFrozenColumnCount"]} if "ordersFrozenColumnCount" in user_config else {}),
            **({"catalogShowRowNumbers": user_config["catalogShowRowNumbers"]} if "catalogShowRowNumbers" in user_config else {}),
            **({"ordersShowRowNumbers": user_config["ordersShowRowNumbers"]} if "ordersShowRowNumbers" in user_config else {}),
            **({"catalogSavedWhereFilters": user_config["catalogSavedWhereFilters"]} if "catalogSavedWhereFilters" in user_config else {}),
            **({"ordersSavedWhereFilters": user_config["ordersSavedWhereFilters"]} if "ordersSavedWhereFilters" in user_config else {}),
            **({"catalogGridPaging": user_config["catalogGridPaging"]} if "catalogGridPaging" in user_config else {}),
            **({"ordersGridPaging": user_config["ordersGridPaging"]} if "ordersGridPaging" in user_config else {}),
            **({"optimalPlanVisibleColumns": user_config["optimalPlanVisibleColumns"]} if "optimalPlanVisibleColumns" in user_config else {}),
            **({"optimalPlanFilterableColumns": user_config["optimalPlanFilterableColumns"]} if "optimalPlanFilterableColumns" in user_config else {}),
            **({"optimalPlanColumnWidths": user_config["optimalPlanColumnWidths"]} if "optimalPlanColumnWidths" in user_config else {}),
            **({"optimalPlanFrozenColumnCount": user_config["optimalPlanFrozenColumnCount"]} if "optimalPlanFrozenColumnCount" in user_config else {}),
            **({"optimalPlanShowRowNumbers": user_config["optimalPlanShowRowNumbers"]} if "optimalPlanShowRowNumbers" in user_config else {}),
            **({"optimalPlanGridPaging": user_config["optimalPlanGridPaging"]} if "optimalPlanGridPaging" in user_config else {}),
            **({"optimalPlanSavedWhereFilters": user_config["optimalPlanSavedWhereFilters"]} if "optimalPlanSavedWhereFilters" in user_config else {}),
        }
        return {"db_name": db_name, "config": merged, "admin_config": admin_config}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.put("/user/db-config/{db_name}")
def set_user_db_config(
    db_name: str,
    payload: UserDbConfigPayload,
    authorization: str = Header(default=""),
) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        user = auth_module.verify_token(token)
        existing = auth_module.get_user_ui_config(user["id"], db_name)
        updates = payload.model_dump(exclude_none=True)
        validated_updates = ui_config_module.validate_user_db_config_updates(updates)
        merged = ui_config_module.normalize_user_db_config({**existing, **validated_updates})
        auth_module.set_user_ui_config(user["id"], db_name, merged)
        return {"db_name": db_name, "config": merged}
    except ValueError as exc:
        message = str(exc)
        if message in {"Token expired", "Invalid token"}:
            raise HTTPException(status_code=401, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc


@app.put("/db-config/{db_name}/progress-colors")
def set_progress_status_colors(
    db_name: str,
    payload: ProgressStatusColorsPayload,
    authorization: str = Header(default=""),
) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.require_db_users_access(token, db_name)
        existing = auth_module.get_db_ui_config(db_name)
        progress_status_colors = ui_config_module.validate_progress_status_colors(
            payload.progressStatusColors,
        )
        merged = {**existing, "progressStatusColors": progress_status_colors}
        auth_module.set_db_ui_config(db_name, merged)
        return {"db_name": db_name, "progressStatusColors": progress_status_colors}
    except ValueError as exc:
        message = str(exc)
        status_code = 401 if message in {"Token expired", "Invalid token"} else 403
        if message.startswith("Invalid hex color"):
            status_code = 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@app.put("/admin/db-config/{db_name}")
def set_db_config(
    db_name: str,
    payload: ui_config_module.DbUiConfigPayload,
    authorization: str = Header(default=""),
) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.require_admin(token)
        config = ui_config_module.validate_db_ui_config(payload.model_dump(), db_name)
        auth_module.set_db_ui_config(db_name, config)
        return {"db_name": db_name, "config": config}
    except ValueError as exc:
        message = str(exc)
        status_code = 403 if message == "Admin access required" else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@app.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_user(user_id: int, authorization: str = Header(default="")) -> Response:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.require_admin(token)
        deleted = auth_module.delete_user(user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="User not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/auth/login")
def login(payload: LoginRequest) -> dict:
    try:
        return auth_module.login_user(email=payload.email, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/assistant/providers", response_model=assistant_module.AssistantProvidersResponse)
def assistant_providers(
    authorization: str = Header(default=""),
) -> assistant_module.AssistantProvidersResponse:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        auth_module.verify_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return assistant_module.list_assistant_providers()


@app.post("/assistant/chat", response_model=assistant_module.AssistantChatResponse)
def assistant_chat(
    payload: assistant_module.AssistantChatRequest,
    authorization: str = Header(default=""),
) -> assistant_module.AssistantChatResponse:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        auth_module.verify_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        return assistant_module.run_assistant_chat(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("assistant chat failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/items/{item_id}/vendor-override")
def set_vendor_override(
    item_id: int,
    payload: VendorOverridePayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        db.set_vendor_override(item_id, payload.vendor_name, db_name)
        return {"item_id": item_id, "vendor_name": payload.vendor_name}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.put("/items/{item_id}/purchasing-method-override")
def set_purchasing_method_override(
    item_id: int,
    payload: PurchasingMethodOverridePayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        db.set_purchasing_method_override(item_id, payload.purchasing_method, db_name)
        item = db.get_item_with_overrides(item_id, db_name)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        return {
            "item_id": item_id,
            "purchasing_method": item.get("purchasing_method"),
            "item_purchasing_method": item.get("item_purchasing_method"),
            "purchasing_method_override_set_at": item.get("purchasing_method_override_set_at"),
        }
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/vendor-names")
def vendor_names(db_name: str = Query(..., alias="db")) -> dict[str, list]:
    try:
        with db.connection(db_name) as conn:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT vendor_name FROM vendor_info ORDER BY vendor_name")
                rows = cursor.fetchall()
        return {"vendor_names": [r["vendor_name"] for r in rows]}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/admin/vendors")
def admin_list_vendors(
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.require_admin(token)
        return {"vendors": db.list_vendors(database=db_name)}
    except ValueError as exc:
        status_code = 403 if "admin" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/admin/vendors", status_code=status.HTTP_201_CREATED)
def admin_add_manual_vendor(
    payload: ManualVendorPayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.require_admin(token)
        vendor = db.add_manual_vendor(payload.vendor_name, database=db_name)
        return {"vendor": vendor}
    except ValueError as exc:
        status_code = 403 if "admin" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/admin/vendors/replace-sync")
def admin_replace_synced_vendors(
    payload: ReplaceSyncedVendorsPayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.require_admin(token)
        stats = db.replace_synced_vendors(payload.vendor_names, database=db_name)
        return stats
    except ValueError as exc:
        status_code = 403 if "admin" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with db.connection() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"status": "ok"}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc.msg}") from exc


@app.get("/databases")
def databases() -> dict[str, list[dict[str, str]]]:
    try:
        return {"databases": db.list_active_databases()}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/tables/{table_name}/columns")
def get_table_columns(
    table_name: str,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        with db.connection(db_name) as conn:
            columns = db.get_columns(conn, table_name)
        return {
            "columns": [c.name for c in columns],
            "column_meta": [
                {
                    "name": c.name,
                    "data_type": c.data_type,
                    "is_nullable": c.is_nullable,
                    "column_key": c.column_key,
                }
                for c in columns
            ],
        }
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/lookup-options")
def lookup_options(
    table: str = Query(...),
    value_column: str = Query(...),
    label_column: str | None = Query(default=None),
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, list[dict[str, str]]]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        options = db.get_lookup_options(
            table_name=table,
            value_column=value_column,
            label_column=label_column,
            database=db_name,
        )
        return {"options": options}
    except ValueError as exc:
        message = str(exc)
        status_code = 401 if message in ("Token expired", "Invalid token") else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/tables")
def tables(db_name: str = Query(default=None, alias="db")) -> dict[str, list[str]]:
    try:
        return {"tables": db.list_tables(db_name)}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/sim-input/{item_id}")
def sim_input(
    item_id: int,
    db_name: str = Query(..., alias="db"),
    number_of_days: int = Query(default=900, ge=1),
    number_of_simulations: int = Query(default=1000, ge=1),
    service_level: float = Query(default=0.95, gt=0, le=1),
    start_day: date | None = Query(default=None),
    end_day: date | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return db.get_sim_input_data(
            item_id=item_id,
            number_of_days=number_of_days,
            number_of_simulations=number_of_simulations,
            service_level=service_level,
            start_day=start_day,
            end_day=end_day,
            database=db_name,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message.startswith("Item not found") else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/sim-result", status_code=status.HTTP_200_OK)
def save_sim_result(
    payload: MultiSimResult,
    db_name: str = Query(..., alias="db"),
) -> dict[str, Any]:
    try:
        rows = [row.model_dump() for row in payload.sim_result]
        count = db.upsert_sim_result(rows, database=db_name)
        return {"saved": count}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/sim-optimal-plan/timeseries", status_code=status.HTTP_200_OK)
def sim_optimal_plan_timeseries(
    payload: SimOptimalPlanTimeseriesPayload,
    db_name: str = Query(..., alias="db"),
) -> dict[str, Any]:
    try:
        series = db.get_sim_optimal_plan_timeseries(
            database=db_name,
            item_ids=payload.item_ids,
        )
        return {"series": series}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/purchase-suggestions", status_code=status.HTTP_200_OK)
def save_purchase_suggestions(
    payload: list[PurchaseSuggestion],
    db_name: str = Query(..., alias="db"),
) -> dict[str, Any]:
    try:
        rows = [s.model_dump() for s in payload]
        count = db.update_purchase_suggestions(rows, database=db_name)
        return {"updated": count}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/purchase-suggestions/reset", status_code=status.HTTP_200_OK)
def reset_purchase_suggestions(
    payload: ResetPurchaseSuggestionsPayload | None = None,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        count = db.reset_purchase_suggestions(
            database=db_name,
            item_ids=payload.item_ids if payload else None,
            source_order_id=payload.source_order_id if payload else None,
        )
        return {"updated": count}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/orders/from-purchase-suggestions", status_code=status.HTTP_201_CREATED)
def create_order_from_purchase_suggestions(
    payload: CreateOrderFromSuggestionsPayload | None = None,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        user = auth_module.verify_token(token)
        result = db.create_order_from_purchase_suggestions(
            database=db_name,
            user_id=user.get("id"),
            description=payload.description if payload else None,
            item_ids=payload.item_ids if payload else None,
            source_order_id=payload.source_order_id if payload else None,
        )
        return {"order": result}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/orders")
def list_orders(
    db_name: str = Query(..., alias="db"),
    limit: int = Query(default=100, ge=1, le=500),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        orders = db.list_orders(database=db_name, limit=limit)
        return {"orders": orders}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/orders/{order_id}/items")
def list_order_items(
    order_id: int,
    db_name: str = Query(..., alias="db"),
    limit: int = Query(default=20000, ge=1, le=20000),
    offset: int = Query(default=0, ge=0),
    order_lines_only: bool = Query(default=True),
    stock_out: bool = Query(default=False),
    sql_grid: str | None = Query(default=None, alias="sqlGrid"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        rows = db.list_order_items(
            order_id=order_id,
            database=db_name,
            limit=limit,
            offset=offset,
            order_lines_only=order_lines_only,
            stock_out=stock_out,
            grid=sql_grid,
        )
        return {"order_id": order_id, "count": len(rows), "rows": rows}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        if getattr(exc, "errno", None) in {1054, 1064, 1066, 1146}:
            raise HTTPException(status_code=400, detail=f"Ógild SQL-sía: {exc.msg}") from exc
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/items/by-number")
def get_item_by_number(
    item_number: str = Query(..., min_length=1),
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        item = db.get_item_by_item_number(item_number=item_number, database=db_name)
        if not item:
            return {"found": False, "item": None}
        return {"found": True, "item": item}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/orders/{order_id}/lines", status_code=status.HTTP_201_CREATED)
def add_order_line(
    order_id: int,
    payload: AddOrderLinePayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        result = db.add_order_line(
            order_id=order_id,
            item_number=payload.item_number,
            qty=payload.qty,
            description=payload.description,
            vendor_name=payload.vendor_name,
            unit_price=payload.unit_price,
            database=db_name,
        )
        return result
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("Order "):
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/orders/{order_id}/merge-from-order", status_code=status.HTTP_200_OK)
def merge_order_lines_from_order(
    order_id: int,
    payload: MergeOrderLinesPayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        auth_module.verify_token(token)
        return db.merge_order_lines_from_order(
            target_order_id=order_id,
            source_order_id=payload.source_order_id,
            progress_statuses=payload.progress_statuses,
            set_progress=payload.set_progress,
            database=db_name,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("Order "):
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/sim-prep")
def sim_prep(
    item_ids: list[int] = Query(...),
    db_name: str = Query(..., alias="db"),
    number_of_days: int = Query(default=900, ge=1),
    number_of_simulations: int = Query(default=1000, ge=1),
    service_level: float = Query(default=0.95, gt=0, le=1),
    start_day: date | None = Query(default=None),
    end_day: date | None = Query(default=None),
) -> list[dict[str, Any]]:
    def fetch_one(item_id: int) -> tuple[int, dict | Exception]:
        try:
            return item_id, db.get_sim_input_data(
                item_id=item_id,
                number_of_days=number_of_days,
                number_of_simulations=number_of_simulations,
                service_level=service_level,
                start_day=start_day,
                end_day=end_day,
                database=db_name,
            )
        except Exception as exc:
            return item_id, exc

    results: dict[int, dict] = {}
    skipped: list[int] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_one, item_id): item_id for item_id in item_ids}
        for future in as_completed(futures):
            item_id, result = future.result()
            if isinstance(result, ValueError):
                message = str(result)
                if message.startswith("Item not found"):
                    skipped.append(item_id)
                    continue
                status_code = 404 if message.startswith("Item not found") else 400
                raise HTTPException(status_code=status_code, detail=message)
            if isinstance(result, mysql.connector.Error):
                raise HTTPException(status_code=500, detail=result.msg)
            if isinstance(result, Exception):
                raise HTTPException(status_code=500, detail=str(result))
            results[item_id] = result

    if skipped:
        logger.warning("sim-prep skipped missing items: %s", skipped)

    return [results[item_id] for item_id in item_ids if item_id in results]


@app.get("/forecast-input/{item_id}")
def forecast_input(
    item_id: int,
    db_name: str = Query(..., alias="db"),
    forecast_periods: int = Query(default=30, ge=1),
    mode: str = Query(default="local"),
    local_model: str = Query(default="auto_arima"),
    season_length: int = Query(default=7, ge=1),
    freq: str = Query(default="D"),
    start_day: date | None = Query(default=None),
    end_day: date | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return db.get_forecast_input_data(
            item_id=item_id,
            forecast_periods=forecast_periods,
            mode=mode,
            local_model=local_model,
            season_length=season_length,
            freq=freq,
            start_day=start_day,
            end_day=end_day,
            database=db_name,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message.startswith("Item not found") else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/tables/{table_name}/ddl")
def get_table_ddl(
    table_name: str,
    db_name: str = Query(..., alias="db"),
) -> dict[str, str]:
    try:
        ddl = db.get_table_ddl(table_name=table_name, database=db_name)
        return {"table": table_name, "ddl": ddl}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/tables/{table_name}/ddl")
def execute_ddl(
    table_name: str,
    payload: dict[str, str],
    db_name: str = Query(..., alias="db"),
) -> dict[str, str]:
    try:
        sql = payload.get("sql", "")
        if not sql:
            raise HTTPException(status_code=400, detail="Missing 'sql' in payload")
        db.execute_ddl(sql=sql, database=db_name)
        return {"status": "ok", "table": table_name}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/tables/{table_name}/rows")
def list_rows(
    table_name: str,
    db_name: str = Query(..., alias="db"),
    limit: int = Query(default=100, ge=1, le=20000),
    offset: int = Query(default=0, ge=0),
    stock_out: bool = Query(default=False),
    sql_grid: str | None = Query(default="catalog", alias="sqlGrid"),
) -> dict[str, Any]:
    try:
        rows = db.list_rows(
            table_name=table_name,
            limit=limit,
            offset=offset,
            database=db_name,
            stock_out=stock_out,
            grid=sql_grid,
        )
        return {"table": table_name, "count": len(rows), "rows": rows}
    except ValueError as exc:
        message = str(exc)
        status_code = 400 if "SQL filter" in message else 404
        raise HTTPException(status_code=status_code, detail=message) from exc
    except mysql.connector.Error as exc:
        if getattr(exc, "errno", None) in {1054, 1064, 1066, 1146}:
            raise HTTPException(status_code=400, detail=f"Ógild SQL-sía: {exc.msg}") from exc
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/tables/{table_name}/rows", status_code=status.HTTP_201_CREATED)
def create_row(
    table_name: str,
    payload: dict[str, Any],
    db_name: str = Query(..., alias="db"),
) -> dict[str, Any]:
    try:
        row = db.create_row(table_name=table_name, payload=payload, database=db_name)
        return {"table": table_name, "row": row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/tables/{table_name}/rows/{row_id}")
def get_row(
    table_name: str,
    row_id: str,
    db_name: str = Query(..., alias="db"),
) -> dict[str, Any]:
    try:
        row = db.get_row(table_name=table_name, row_id=row_id, database=db_name)
        if not row:
            raise HTTPException(status_code=404, detail="Row not found")
        return {"table": table_name, "row": row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.put("/tables/{table_name}/rows/{row_id}")
def update_row(
    table_name: str,
    row_id: str,
    payload: dict[str, Any],
    db_name: str = Query(..., alias="db"),
) -> dict[str, Any]:
    try:
        row = db.update_row(table_name=table_name, row_id=row_id, payload=payload, database=db_name)
        if not row:
            raise HTTPException(status_code=404, detail="Row not found")
        return {"table": table_name, "row": row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.delete("/tables/{table_name}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_row(
    table_name: str,
    row_id: str,
    db_name: str = Query(..., alias="db"),
) -> Response:
    try:
        deleted = db.delete_row(table_name=table_name, row_id=row_id, database=db_name)
        if not deleted:
            raise HTTPException(status_code=404, detail="Row not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc
