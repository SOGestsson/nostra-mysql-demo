from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ENGINE_URL = os.getenv("FORECAST_API_URL", "https://api.nostradamus-api.com")
ENGINE_TIMEOUT = float(os.getenv("FORECAST_API_TIMEOUT", "120"))

GENERATE_PATH = "/api/v1/forecast/generate"

SUPPORTED_MODELS = [
    "auto_model",
    "auto_arima",
    "auto_ets",
    "auto_ces",
    "croston_optimized",
    "adida",
    "theta",
    "optimized_theta",
    "naive",
    "seasonal_naive",
]

UPPER_BOUND_KEYS = ("upper_70", "upper_90", "upper_95")


class ForecastEngineError(Exception):
    """Raised when the forecast engine is unreachable or returns an error."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_model(local_model: str, mode: str) -> None:
    if local_model not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported local_model '{local_model}'. Supported: {', '.join(SUPPORTED_MODELS)}"
        )
    if local_model in ("auto_model", "automodel") and mode != "local":
        raise ValueError("local_model='auto_model' requires mode='local'")


def generate(payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{ENGINE_URL.rstrip('/')}{GENERATE_PATH}"
    try:
        response = httpx.post(url, json=payload, timeout=ENGINE_TIMEOUT)
    except httpx.TimeoutException as exc:
        raise ForecastEngineError(
            f"Forecast engine timed out after {ENGINE_TIMEOUT}s", status_code=504
        ) from exc
    except httpx.HTTPError as exc:
        raise ForecastEngineError(f"Forecast engine unreachable: {exc}") from exc

    if response.status_code >= 400:
        detail = _error_detail(response)
        raise ForecastEngineError(
            f"Forecast engine returned {response.status_code}: {detail}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ForecastEngineError("Forecast engine returned a non-JSON response") from exc


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(body, dict):
        return str(body.get("detail", body))[:500]
    return str(body)[:500]


def parse_response(envelope: dict[str, Any], freq: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Flatten the engine envelope into forecast_result rows plus per-item failures.

    The engine reports per-item failures inside `forecasts` rather than failing the
    whole request, so those are separated out instead of raising.
    """
    rows: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for item in envelope.get("forecasts") or []:
        item_id = _coerce_item_id(item.get("item_id"))

        if item.get("error"):
            failed.append({"item_id": item_id, "error": str(item["error"])})
            continue

        dates = item.get("forecast_dates") or []
        values = item.get("forecast") or []
        if not dates or not values:
            failed.append({"item_id": item_id, "error": "Engine returned an empty forecast"})
            continue

        model_used = item.get("model_used")
        bounds = {key: item.get(key) or [] for key in UPPER_BOUND_KEYS}

        for index, forecast_date in enumerate(dates):
            if index >= len(values):
                break
            rows.append(
                {
                    "item_id": item_id,
                    "forecast_date": forecast_date,
                    "forecast": _to_float(values[index]),
                    "upper_70": _bound_at(bounds["upper_70"], index),
                    "upper_90": _bound_at(bounds["upper_90"], index),
                    "upper_95": _bound_at(bounds["upper_95"], index),
                    "model_used": str(model_used) if model_used else None,
                    "freq": freq,
                }
            )

    return rows, failed


def _coerce_item_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Forecast engine returned a non-numeric item_id: %r", value)
        return None


def _bound_at(values: list[Any], index: int) -> float | None:
    if index >= len(values):
        return None
    return _to_float(values[index])


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def health() -> dict[str, Any]:
    url = f"{ENGINE_URL.rstrip('/')}/health"
    try:
        response = httpx.get(url, timeout=10.0)
    except httpx.HTTPError as exc:
        return {"engine": "unreachable", "url": ENGINE_URL, "error": str(exc)}
    if response.status_code >= 400:
        return {"engine": "error", "url": ENGINE_URL, "status": response.status_code}
    return {"engine": "ok", "url": ENGINE_URL}
