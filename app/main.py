from __future__ import annotations

from datetime import date
from typing import Any

import mysql.connector
from fastapi import FastAPI, HTTPException, Query, Response, status

from app import db


app = FastAPI(title="Nostra MySQL CRUD API")


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with db.connection() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"status": "ok"}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc.msg}") from exc


@app.get("/tables")
def tables() -> dict[str, list[str]]:
    try:
        return {"tables": db.list_tables()}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/sim-input/{item_id}")
def sim_input(
    item_id: int,
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
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        rows = db.list_rows(table_name=table_name, limit=limit, offset=offset)
        return {"table": table_name, "count": len(rows), "rows": rows}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.post("/tables/{table_name}/rows", status_code=status.HTTP_201_CREATED)
def create_row(table_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        row = db.create_row(table_name=table_name, payload=payload)
        return {"table": table_name, "row": row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.get("/tables/{table_name}/rows/{row_id}")
def get_row(table_name: str, row_id: str) -> dict[str, Any]:
    try:
        row = db.get_row(table_name=table_name, row_id=row_id)
        if not row:
            raise HTTPException(status_code=404, detail="Row not found")
        return {"table": table_name, "row": row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.put("/tables/{table_name}/rows/{row_id}")
def update_row(table_name: str, row_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        row = db.update_row(table_name=table_name, row_id=row_id, payload=payload)
        if not row:
            raise HTTPException(status_code=404, detail="Row not found")
        return {"table": table_name, "row": row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc


@app.delete("/tables/{table_name}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_row(table_name: str, row_id: str) -> Response:
    try:
        deleted = db.delete_row(table_name=table_name, row_id=row_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Row not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=exc.msg) from exc
