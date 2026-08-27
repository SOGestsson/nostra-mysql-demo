from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

import mysql.connector
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import db, auth as auth_module, forecast as forecast_module, ui_config as ui_config_module, assistant as assistant_module
from app.security import check_login_rate, client_ip, cors_allow_origins, docs_enabled, require_request_user

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


_ENABLE_DOCS = docs_enabled()
app = FastAPI(
    title="Nostra MySQL CRUD API",
    docs_url="/docs" if _ENABLE_DOCS else None,
    redoc_url="/redoc" if _ENABLE_DOCS else None,
    openapi_url="/openapi.json" if _ENABLE_DOCS else None,
)

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
    forecastsVisibleColumns: list[str] | None = None
    forecastsFilterableColumns: list[str] | None = None
    forecastsColumnWidths: dict[str, int] | None = None
    forecastsFrozenColumnCount: int | None = None
    forecastsShowRowNumbers: bool | None = None
    forecastsGridPaging: str | None = None
    forecastsSavedWhereFilters: list[ui_config_module.SavedWhereFilter] | None = None
    roiVisibleColumns: list[str] | None = None
    roiFilterableColumns: list[str] | None = None
    roiColumnWidths: dict[str, int] | None = None
    roiFrozenColumnCount: int | None = None
    roiShowRowNumbers: bool | None = None
    roiGridPaging: str | None = None
    roiSavedWhereFilters: list[ui_config_module.SavedWhereFilter] | None = None


class ProgressStatusColorsPayload(BaseModel):
    progressStatusColors: dict[str, str] = Field(default_factory=dict)


class ManualVendorPayload(BaseModel):
    vendor_name: str


class ForecastBatchPayload(BaseModel):
    item_ids: list[int]


class RoiBatchPayload(BaseModel):
    item_ids: list[int]


class ReplaceSyncedVendorsPayload(BaseModel):
    vendor_names: list[str] = []


@app.on_event("startup")
def startup() -> None:
    auth_module.ensure_users_table()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, authorization: str = Header(default="")) -> dict:
    require_request_user(authorization, admin=True)
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
    require_request_user(authorization, admin=True)
    return {"users": auth_module.list_users()}


@app.get("/admin/users/{user_id}/login-history")
def admin_login_history(
    user_id: int,
    authorization: str = Header(default=""),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    require_request_user(authorization, admin=True)
    return {"user_id": user_id, "history": auth_module.list_login_history(user_id, limit=limit)}


@app.get("/db-users")
def list_db_users(
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict:
    require_request_user(authorization, db_name)
    return {"users": auth_module.list_users_for_database(db_name)}


@app.post("/admin/users", status_code=status.HTTP_201_CREATED)
def admin_create_user(payload: RegisterRequest, authorization: str = Header(default="")) -> dict:
    require_request_user(authorization, admin=True)
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


@app.get("/db-config/{db_name}")
def get_db_config(db_name: str, authorization: str = Header(default="")) -> dict:
    require_request_user(authorization, db_name)
    return {"db_name": db_name, "config": auth_module.get_db_ui_config(db_name)}


@app.get("/user/db-config/{db_name}")
def get_user_db_config(db_name: str, authorization: str = Header(default="")) -> dict:
    user = require_request_user(authorization, db_name)
    try:
        admin_config = auth_module.get_db_ui_config(db_name)
        user_config = ui_config_module.normalize_user_db_config(
            auth_module.get_user_ui_config(user["id"], db_name),
        )
        merged = {
            **admin_config,
            **{
                key: user_config[key]
                for key in ui_config_module.USER_DB_CONFIG_FIELDS
                if key in user_config
            },
        }
        return {"db_name": db_name, "config": merged, "admin_config": admin_config}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/user/db-config/{db_name}")
def set_user_db_config(
    db_name: str,
    payload: UserDbConfigPayload,
    authorization: str = Header(default=""),
) -> dict:
    user = require_request_user(authorization, db_name)
    try:
        existing = auth_module.get_user_ui_config(user["id"], db_name)
        updates = payload.model_dump(exclude_none=True)
        validated_updates = ui_config_module.validate_user_db_config_updates(updates)
        merged = ui_config_module.normalize_user_db_config({**existing, **validated_updates})
        auth_module.set_user_ui_config(user["id"], db_name, merged)
        return {"db_name": db_name, "config": merged}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/db-config/{db_name}/progress-colors")
def set_progress_status_colors(
    db_name: str,
    payload: ProgressStatusColorsPayload,
    authorization: str = Header(default=""),
) -> dict:
    require_request_user(authorization, db_name)
    try:
        existing = auth_module.get_db_ui_config(db_name)
        progress_status_colors = ui_config_module.validate_progress_status_colors(
            payload.progressStatusColors,
        )
        merged = {**existing, "progressStatusColors": progress_status_colors}
        auth_module.set_db_ui_config(db_name, merged)
        return {"db_name": db_name, "progressStatusColors": progress_status_colors}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/admin/db-config/{db_name}")
def set_db_config(
    db_name: str,
    payload: ui_config_module.DbUiConfigPayload,
    authorization: str = Header(default=""),
) -> dict:
    require_request_user(authorization, db_name, admin=True)
    try:
        config = ui_config_module.validate_db_ui_config(payload.model_dump(), db_name)
        auth_module.set_db_ui_config(db_name, config)
        return {"db_name": db_name, "config": config}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_user(user_id: int, authorization: str = Header(default="")) -> Response:
    require_request_user(authorization, admin=True)
    deleted = auth_module.delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/auth/seen")
def mark_seen(authorization: str = Header(default="")) -> dict:
    user = require_request_user(authorization)
    auth_module.touch_last_seen(user["id"])
    return {"ok": True}


@app.post("/auth/login")
def login(payload: LoginRequest, request: Request) -> dict:
    ip = client_ip(request)
    check_login_rate(ip)
    user_agent = request.headers.get("user-agent") or ""
    try:
        result = auth_module.login_user(email=payload.email, password=payload.password)
        auth_module.record_login(
            email=payload.email,
            success=True,
            ip=ip,
            user_agent=user_agent,
            user_id=result["user"]["id"],
        )
        return result
    except ValueError as exc:
        auth_module.record_login(
            email=payload.email,
            success=False,
            ip=ip,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/assistant/providers", response_model=assistant_module.AssistantProvidersResponse)
def assistant_providers(
    authorization: str = Header(default=""),
) -> assistant_module.AssistantProvidersResponse:
    require_request_user(authorization)
    return assistant_module.list_assistant_providers()


@app.post("/assistant/chat", response_model=assistant_module.AssistantChatResponse)
def assistant_chat(
    payload: assistant_module.AssistantChatRequest,
    authorization: str = Header(default=""),
) -> assistant_module.AssistantChatResponse:
    require_request_user(authorization)
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
    require_request_user(authorization, db_name)
    try:
        db.set_vendor_override(item_id, payload.vendor_name, db_name)
        return {"item_id": item_id, "vendor_name": payload.vendor_name}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.put("/items/{item_id}/purchasing-method-override")
def set_purchasing_method_override(
    item_id: int,
    payload: PurchasingMethodOverridePayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict:
    require_request_user(authorization, db_name)
    try:
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
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/vendor-names")
def vendor_names(
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, list]:
    require_request_user(authorization, db_name)
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
    require_request_user(authorization, db_name, admin=True)
    try:
        return {"vendors": db.list_vendors(database=db_name)}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/admin/vendors", status_code=status.HTTP_201_CREATED)
def admin_add_manual_vendor(
    payload: ManualVendorPayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name, admin=True)
    try:
        vendor = db.add_manual_vendor(payload.vendor_name, database=db_name)
        return {"vendor": vendor}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/admin/vendors/replace-sync")
def admin_replace_synced_vendors(
    payload: ReplaceSyncedVendorsPayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name, admin=True)
    try:
        stats = db.replace_synced_vendors(payload.vendor_names, database=db_name)
        return stats
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/health")
def health(check_engine: bool = Query(default=False)) -> dict[str, Any]:
    try:
        with db.connection() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc.msg}") from exc

    if check_engine:
        return {"status": "ok", "forecast_engine": forecast_module.health()}
    return {"status": "ok"}


@app.get("/databases")
def databases(authorization: str = Header(default="")) -> dict[str, list[dict[str, str]]]:
    user = require_request_user(authorization)
    try:
        all_dbs = db.list_active_databases()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc
    if user.get("is_admin"):
        return {"databases": all_dbs}
    allowed = user.get("database_name")
    return {"databases": [row for row in all_dbs if row.get("name") == allowed]}


@app.get("/tables/{table_name}/columns")
def get_table_columns(
    table_name: str,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    require_request_user(authorization, db_name)
    try:
        options = db.get_lookup_options(
            table_name=table,
            value_column=value_column,
            label_column=label_column,
            database=db_name,
        )
        return {"options": options}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/tables")
def tables(
    db_name: str = Query(default=None, alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, list[str]]:
    require_request_user(authorization, db_name)
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
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
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
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
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
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        series = db.get_sim_optimal_plan_timeseries(
            database=db_name,
            item_ids=payload.item_ids,
        )
        return {"series": series}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.post("/purchase-suggestions", status_code=status.HTTP_200_OK)
def save_purchase_suggestions(
    payload: list[PurchaseSuggestion],
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        rows = [s.model_dump() for s in payload]
        count = db.update_purchase_suggestions(rows, database=db_name)
        return {"updated": count}
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.post("/purchase-suggestions/reset", status_code=status.HTTP_200_OK)
def reset_purchase_suggestions(
    payload: ResetPurchaseSuggestionsPayload | None = None,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        count = db.reset_purchase_suggestions(
            database=db_name,
            item_ids=payload.item_ids if payload else None,
            source_order_id=payload.source_order_id if payload else None,
        )
        return {"updated": count}
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.post("/orders/from-purchase-suggestions", status_code=status.HTTP_201_CREATED)
def create_order_from_purchase_suggestions(
    payload: CreateOrderFromSuggestionsPayload | None = None,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    user = require_request_user(authorization, db_name)
    try:
        result = db.create_order_from_purchase_suggestions(
            database=db_name,
            user_id=user.get("id"),
            description=payload.description if payload else None,
            item_ids=payload.item_ids if payload else None,
            source_order_id=payload.source_order_id if payload else None,
        )
        return {"order": result}
    except ValueError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.get("/orders")
def list_orders(
    db_name: str = Query(..., alias="db"),
    limit: int = Query(default=100, ge=1, le=500),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        orders = db.list_orders(database=db_name, limit=limit)
        return {"orders": orders}
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


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
    require_request_user(authorization, db_name)
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
    except ValueError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra
    except mysql.connector.Error as extra:
        if getattr(extra, "errno", None) in {1054, 1064, 1066, 1146}:
            raise HTTPException(status_code=400, detail=f"Ógild SQL-sía: {extra.msg}") from extra
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.get("/items/by-number")
def get_item_by_number(
    item_number: str = Query(..., min_length=1),
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        item = db.get_item_by_item_number(item_number=item_number, database=db_name)
        if not item:
            return {"found": False, "item": None}
        return {"found": True, "item": item}
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.post("/orders/{order_id}/lines", status_code=status.HTTP_201_CREATED)
def add_order_line(
    order_id: int,
    payload: AddOrderLinePayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
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
    except ValueError as extra:
        detail = str(extra)
        if detail.startswith("Order "):
            raise HTTPException(status_code=404, detail=detail) from extra
        raise HTTPException(status_code=400, detail=detail) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.post("/orders/{order_id}/merge-from-order", status_code=status.HTTP_200_OK)
def merge_order_lines_from_order(
    order_id: int,
    payload: MergeOrderLinesPayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        return db.merge_order_lines_from_order(
            target_order_id=order_id,
            source_order_id=payload.source_order_id,
            progress_statuses=payload.progress_statuses,
            set_progress=payload.set_progress,
            database=db_name,
        )
    except ValueError as extra:
        detail = str(extra)
        if detail.startswith("Order "):
            raise HTTPException(status_code=404, detail=detail) from extra
        raise HTTPException(status_code=400, detail=detail) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.get("/sim-prep")
def sim_prep(
    item_ids: list[int] = Query(...),
    db_name: str = Query(..., alias="db"),
    number_of_days: int = Query(default=900, ge=1),
    number_of_simulations: int = Query(default=1000, ge=1),
    service_level: float = Query(default=0.95, gt=0, le=1),
    start_day: date | None = Query(default=None),
    end_day: date | None = Query(default=None),
    authorization: str = Header(default=""),
) -> list[dict[str, Any]]:
    require_request_user(authorization, db_name)

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
        except Exception as extra:
            return item_id, extra

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
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
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
    except ValueError as extra:
        message = str(extra)
        status_code = 404 if message.startswith("Item not found") else 400
        raise HTTPException(status_code=status_code, detail=message) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.get("/forecast/models")
def forecast_models(authorization: str = Header(default="")) -> dict[str, Any]:
    require_request_user(authorization)
    return {"models": forecast_module.SUPPORTED_MODELS}


@app.post("/forecast/run/{item_id}")
def run_forecast(
    item_id: int,
    db_name: str = Query(..., alias="db"),
    forecast_periods: int = Query(default=30, ge=1),
    mode: str = Query(default="local"),
    local_model: str = Query(default="auto_arima"),
    season_length: int = Query(default=7, ge=1),
    freq: str = Query(default="D"),
    start_day: date | None = Query(default=None),
    end_day: date | None = Query(default=None),
    persist: bool = Query(default=True),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        forecast_module.validate_model(local_model, mode)

        payload = db.get_forecast_input_data(
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
        envelope = forecast_module.generate(payload)
        rows, failed = forecast_module.parse_response(envelope, freq)

        saved = db.upsert_forecast_result(rows, database=db_name) if persist and rows else 0
        return {
            "item_id": item_id,
            "persisted": bool(persist and rows),
            "saved": saved,
            "model_used": rows[0]["model_used"] if rows else None,
            "forecast": rows,
            "failed": failed,
        }
    except forecast_module.ForecastEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        if message in ("Token expired", "Invalid token"):
            status_code = 401
        elif message.startswith("Item not found"):
            status_code = 404
        else:
            status_code = 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/forecast/run-batch")
def run_forecast_batch(
    payload: ForecastBatchPayload,
    db_name: str = Query(..., alias="db"),
    forecast_periods: int = Query(default=30, ge=1),
    mode: str = Query(default="local"),
    local_model: str = Query(default="auto_arima"),
    season_length: int = Query(default=7, ge=1),
    freq: str = Query(default="D"),
    start_day: date | None = Query(default=None),
    end_day: date | None = Query(default=None),
    persist: bool = Query(default=True),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    try:
        require_request_user(authorization, db_name)
        forecast_module.validate_model(local_model, mode)
    except ValueError as exc:
        message = str(exc)
        status_code = 401 if message in ("Token expired", "Invalid token") else 400
        raise HTTPException(status_code=status_code, detail=message) from exc

    if not payload.item_ids:
        raise HTTPException(status_code=400, detail="item_ids must not be empty")

    def build_one(item_id: int) -> tuple[int, dict | Exception]:
        try:
            return item_id, db.get_forecast_input_data(
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
        except Exception as exc:
            return item_id, exc

    history: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(build_one, item_id) for item_id in payload.item_ids]
        for future in as_completed(futures):
            item_id, result = future.result()
            if isinstance(result, Exception):
                skipped.append({"item_id": item_id, "error": str(result)})
                continue
            history.extend(result.get("sim_input_his") or [])

    if not history:
        return {"saved": 0, "items": 0, "skipped": skipped, "failed": []}

    engine_payload = {
        "sim_input_his": history,
        "forecast_periods": forecast_periods,
        "mode": mode,
        "local_model": local_model,
        "season_length": season_length,
        "freq": freq,
    }

    try:
        envelope = forecast_module.generate(engine_payload)
        rows, failed = forecast_module.parse_response(envelope, freq)
        saved = db.upsert_forecast_result(rows, database=db_name) if persist and rows else 0
    except forecast_module.ForecastEngineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc

    if skipped:
        logger.warning("forecast run-batch skipped items: %s", [s["item_id"] for s in skipped])

    return {
        "saved": saved,
        "persisted": bool(persist and rows),
        "items": len({row["item_id"] for row in rows}),
        "skipped": skipped,
        "failed": failed,
    }


@app.get("/forecast/{item_id}")
def get_forecast(
    item_id: int,
    db_name: str = Query(..., alias="db"),
    limit: int = Query(default=1000, ge=1, le=20000),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        rows = db.get_forecast_result(item_id=item_id, database=db_name, limit=limit)
        return {"item_id": item_id, "count": len(rows), "rows": rows}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


def _roi_run_params(
    service_level: float,
    use_item_service_level: bool,
    ss_source: str,
) -> dict[str, Any]:
    source = (ss_source or "forecast").strip().lower()
    if source not in {"forecast", "override"}:
        raise ValueError("ss_source must be 'forecast' or 'override'")
    if not 0 < service_level < 1:
        raise ValueError("service_level must be between 0 and 1")
    return {
        "service_level": service_level,
        "use_item_service_level": use_item_service_level,
        "ss_source": source,
    }


@app.post("/roi/run/{item_id}")
def run_roi(
    item_id: int,
    db_name: str = Query(..., alias="db"),
    service_level: float = Query(default=0.95, gt=0, lt=1),
    use_item_service_level: bool = Query(default=False),
    ss_source: str = Query(default="forecast"),
    persist: bool = Query(default=True),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        params = _roi_run_params(service_level, use_item_service_level, ss_source)
        row = db.run_roi_point_estimate(
            item_id,
            database=db_name,
            persist=persist,
            **params,
        )
        return {"item_id": item_id, "saved": row.get("saved") or 0, "row": row}
    except ValueError as exc:
        message = str(exc)
        if message in ("Token expired", "Invalid token"):
            status_code = 401
        elif message.startswith("Item not found"):
            status_code = 404
        else:
            status_code = 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/roi/run-batch")
def run_roi_batch(
    payload: RoiBatchPayload,
    db_name: str = Query(..., alias="db"),
    service_level: float = Query(default=0.95, gt=0, lt=1),
    use_item_service_level: bool = Query(default=False),
    ss_source: str = Query(default="forecast"),
    persist: bool = Query(default=True),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        params = _roi_run_params(service_level, use_item_service_level, ss_source)
    except ValueError as exc:
        message = str(exc)
        status_code = 401 if message in ("Token expired", "Invalid token") else 400
        raise HTTPException(status_code=status_code, detail=message) from exc

    if not payload.item_ids:
        raise HTTPException(status_code=400, detail="item_ids must not be empty")

    saved = 0
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item_id in payload.item_ids:
        try:
            row = db.run_roi_point_estimate(
                item_id,
                database=db_name,
                persist=persist,
                **params,
            )
            saved += int(row.get("saved") or 0)
            rows.append(row)
        except ValueError as exc:
            skipped.append({"item_id": item_id, "error": str(exc)})
        except Exception as exc:
            failed.append({"item_id": item_id, "error": str(exc)})

    return {
        "saved": saved,
        "items": len(rows),
        "skipped": skipped,
        "failed": failed,
    }


@app.post("/roi/overview")
def roi_overview(
    payload: RoiBatchPayload,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        return db.roi_overview(payload.item_ids, database=db_name)
    except ValueError as exc:
        status_code = 401 if str(exc) in ("Token expired", "Invalid token") else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/roi/{item_id}/stock-history")
def get_roi_stock_history(
    item_id: int,
    db_name: str = Query(..., alias="db"),
    limit: int = Query(default=1000, ge=1, le=20000),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        rows = db.get_stock_history(item_id=item_id, database=db_name, limit=limit)
        return {"item_id": item_id, "count": len(rows), "rows": rows}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/roi/{item_id}")
def get_roi(
    item_id: int,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        row = db.get_roi_result(item_id=item_id, database=db_name)
        return {"item_id": item_id, "row": row}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/tables/{table_name}/ddl")
def get_table_ddl(
    table_name: str,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, str]:
    require_request_user(authorization, db_name, admin=True)
    try:
        ddl = db.get_table_ddl(table_name=table_name, database=db_name)
        return {"table": table_name, "ddl": ddl}
    except ValueError as extra:
        raise HTTPException(status_code=404, detail=str(extra)) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.post("/tables/{table_name}/ddl")
def execute_ddl(
    table_name: str,
    payload: dict[str, str],
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, str]:
    require_request_user(authorization, db_name, admin=True)
    try:
        sql = payload.get("sql", "")
        if not sql:
            raise HTTPException(status_code=400, detail="Missing 'sql' in payload")
        db.execute_ddl(sql=sql, database=db_name)
        return {"status": "ok", "table": table_name}
    except ValueError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.get("/tables/{table_name}/rows")
def list_rows(
    table_name: str,
    db_name: str = Query(..., alias="db"),
    limit: int = Query(default=100, ge=1, le=20000),
    offset: int = Query(default=0, ge=0),
    stock_out: bool = Query(default=False),
    sql_grid: str | None = Query(default="catalog", alias="sqlGrid"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
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
    except ValueError as extra:
        message = str(extra)
        status_code = 400 if "SQL filter" in message else 404
        raise HTTPException(status_code=status_code, detail=message) from extra
    except mysql.connector.Error as extra:
        if getattr(extra, "errno", None) in {1054, 1064, 1066, 1146}:
            raise HTTPException(status_code=400, detail=f"Ógild SQL-sía: {extra.msg}") from extra
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.post("/tables/{table_name}/rows", status_code=status.HTTP_201_CREATED)
def create_row(
    table_name: str,
    payload: dict[str, Any],
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        row = db.create_row(table_name=table_name, payload=payload, database=db_name)
        return {"table": table_name, "row": row}
    except ValueError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.get("/tables/{table_name}/rows/{row_id}")
def get_row(
    table_name: str,
    row_id: str,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        row = db.get_row(table_name=table_name, row_id=row_id, database=db_name)
        if not row:
            raise HTTPException(status_code=404, detail="Row not found")
        return {"table": table_name, "row": row}
    except ValueError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.put("/tables/{table_name}/rows/{row_id}")
def update_row(
    table_name: str,
    row_id: str,
    payload: dict[str, Any],
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    require_request_user(authorization, db_name)
    try:
        row = db.update_row(table_name=table_name, row_id=row_id, payload=payload, database=db_name)
        if not row:
            raise HTTPException(status_code=404, detail="Row not found")
        return {"table": table_name, "row": row}
    except ValueError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra


@app.delete("/tables/{table_name}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_row(
    table_name: str,
    row_id: str,
    db_name: str = Query(..., alias="db"),
    authorization: str = Header(default=""),
) -> Response:
    require_request_user(authorization, db_name)
    try:
        deleted = db.delete_row(table_name=table_name, row_id=row_id, database=db_name)
        if not deleted:
            raise HTTPException(status_code=404, detail="Row not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValueError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra
    except mysql.connector.Error as extra:
        raise HTTPException(status_code=500, detail=extra.msg) from extra
