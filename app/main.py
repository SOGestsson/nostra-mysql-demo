from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

import mysql.connector
from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import db


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


app = FastAPI(title="Nostra MySQL CRUD API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_one, item_id): item_id for item_id in item_ids}
        for future in as_completed(futures):
            item_id, result = future.result()
            if isinstance(result, ValueError):
                message = str(result)
                status_code = 404 if message.startswith("Item not found") else 400
                raise HTTPException(status_code=status_code, detail=message)
            if isinstance(result, mysql.connector.Error):
                raise HTTPException(status_code=500, detail=result.msg)
            if isinstance(result, Exception):
                raise HTTPException(status_code=500, detail=str(result))
            results[item_id] = result

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


@app.get("/tables/{table_name}/rows")
def list_rows(
    table_name: str,
    db_name: str = Query(..., alias="db"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        rows = db.list_rows(table_name=table_name, limit=limit, offset=offset, database=db_name)
        return {"table": table_name, "count": len(rows), "rows": rows}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
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
