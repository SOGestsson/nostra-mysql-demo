"""Point-estimate expected stock from stored forecasts.

Conservative target:
    expected_stock = forecast(L) + Q/2 + SS(P)

where L is lead time (del_time), T is buy_freq, P = L + T, and Q is demand over T
after MOQ / order-multiple floors. Safety stock uses implied sigma from the
stored 70/90/95 upper bounds — quantiles are never summed across periods.
"""

from __future__ import annotations

import math
from typing import Any

Z_70 = 0.5244005127080407
Z_90 = 1.2815515655446004
Z_95 = 1.6448536269514722

BOUND_Z = (
    ("upper_70", Z_70),
    ("upper_90", Z_90),
    ("upper_95", Z_95),
)

DAYS_PER_PERIOD = {
    "D": 1.0,
    "W": 7.0,
    "M": 30.4375,
    "MS": 30.4375,
    "ME": 30.4375,
    "Q": 91.3125,
    "QS": 91.3125,
    "QE": 91.3125,
    "Y": 365.25,
    "A": 365.25,
    "YS": 365.25,
    "YE": 365.25,
}


def days_per_period(freq: str | None) -> float:
    key = str(freq or "M").strip().upper()
    if key.startswith("W"):
        return DAYS_PER_PERIOD["W"]
    return DAYS_PER_PERIOD.get(key, DAYS_PER_PERIOD["M"])


def normsinv(p: float) -> float:
    """Acklam inverse normal CDF for p in (0, 1)."""
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")
    if abs(p - 0.5) < 1e-15:
        return 0.0

    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577509590705e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464858e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )

    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    ) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def implied_sigma(row: dict[str, Any]) -> float:
    mu = _to_float(row.get("forecast"), 0.0) or 0.0
    sigmas: list[float] = []
    for key, z in BOUND_Z:
        upper = _to_float(row.get(key))
        if upper is None or z <= 0:
            continue
        if upper > mu:
            sigmas.append((upper - mu) / z)
    if not sigmas:
        return 0.0
    sigmas.sort()
    mid = len(sigmas) // 2
    if len(sigmas) % 2:
        return sigmas[mid]
    return 0.5 * (sigmas[mid - 1] + sigmas[mid])


def window_stats(
    rows: list[dict[str, Any]],
    days: float,
    period_days: float,
) -> tuple[float, float]:
    """Mean and sigma over the first `days` of a regular forecast path."""
    remaining = max(float(days or 0), 0.0)
    if remaining <= 0 or period_days <= 0 or not rows:
        return 0.0, 0.0
    mean = 0.0
    variance = 0.0
    for row in rows:
        if remaining <= 0:
            break
        frac = min(1.0, remaining / period_days)
        mu = _to_float(row.get("forecast"), 0.0) or 0.0
        sigma = implied_sigma(row)
        mean += frac * mu
        variance += frac * (sigma ** 2)
        remaining -= period_days
    return mean, math.sqrt(max(variance, 0.0))


def apply_lot_size(demand_t: float, moq: float | None, order_multiple: float | None) -> float:
    qty = max(float(demand_t or 0), 0.0)
    if moq is not None and moq > qty:
        qty = float(moq)
    multiple = float(order_multiple or 0)
    if multiple > 1:
        qty = math.ceil(qty / multiple - 1e-12) * multiple
    return qty


def point_estimate(
    item: dict[str, Any],
    forecast_rows: list[dict[str, Any]],
    stock_avgs: dict[str, float | None] | None = None,
    *,
    service_level: float = 0.95,
    use_item_service_level: bool = False,
    ss_source: str = "forecast",
) -> dict[str, Any]:
    if not forecast_rows:
        raise ValueError("No stored forecast for this item")

    rows = sorted(
        forecast_rows,
        key=lambda row: str(row.get("forecast_date") or ""),
    )
    freq = str(rows[0].get("freq") or "M")
    period_days = days_per_period(freq)
    model_used = next((row.get("model_used") for row in rows if row.get("model_used")), None)

    lead_days = max(_to_float(item.get("del_time"), 0.0) or 0.0, 0.0)
    order_days = max(_to_float(item.get("buy_freq"), 0.0) or 0.0, 0.0)
    if lead_days <= 0 and order_days <= 0:
        raise ValueError("Item is missing del_time and buy_freq")

    item_sl = _to_float(item.get("service_level"))
    sl = item_sl if use_item_service_level and item_sl and 0 < item_sl < 1 else service_level
    if sl is None or not 0 < float(sl) < 1:
        sl = 0.95
    sl = float(sl)
    z_target = normsinv(sl)

    demand_l, _ = window_stats(rows, lead_days, period_days)
    demand_t, _ = window_stats(rows, order_days, period_days)
    _, sigma_p = window_stats(rows, lead_days + order_days, period_days)
    ss_forecast = max(z_target * sigma_p, 0.0)

    moq = _to_float(item.get("moq"))
    order_multiple = _to_float(item.get("order_multiple"))
    order_qty = apply_lot_size(demand_t, moq, order_multiple)
    cycle_stock = order_qty / 2.0

    source = str(ss_source or "forecast").strip().lower()
    override = _to_float(item.get("safety_stock"))
    used_source = "forecast"
    ss_used = ss_forecast
    if source == "override" and override is not None:
        used_source = "override"
        ss_used = max(override, 0.0)

    unit_cost = _to_float(item.get("unit_cost"))
    if unit_cost is None or unit_cost == 0:
        unit_cost = _to_float(item.get("price"), 0.0) or 0.0
    current_stock = _to_float(item.get("stock_level"), 0.0) or 0.0
    expected_stock = demand_l + cycle_stock + ss_used
    expected_value = expected_stock * unit_cost
    current_value = current_stock * unit_cost

    avgs = stock_avgs or {}
    avg_3 = _to_float(avgs.get("avg_stock_3m"))
    avg_6 = _to_float(avgs.get("avg_stock_6m"))
    avg_12 = _to_float(avgs.get("avg_stock_12m"))

    return {
        "item_id": int(item["id"]),
        "method": "point_estimate",
        "model_used": str(model_used) if model_used else None,
        "forecast_freq": freq,
        "service_level": sl,
        "ss_source": used_source,
        "ss_override": override if used_source == "override" else None,
        "unit_cost": unit_cost,
        "del_time": lead_days,
        "buy_freq": order_days,
        "moq": moq,
        "order_multiple": order_multiple,
        "cover_days": lead_days + order_days,
        "order_period_days": order_days,
        "forecast_lead_qty": demand_l,
        "forecast_order_qty": demand_t,
        "order_qty": order_qty,
        "cycle_stock": cycle_stock,
        "safety_stock_forecast": ss_forecast,
        "safety_stock_used": ss_used,
        "expected_stock": expected_stock,
        "expected_value": expected_value,
        "current_stock": current_stock,
        "current_value": current_value,
        "avg_stock_3m": avg_3,
        "avg_stock_6m": avg_6,
        "avg_stock_12m": avg_12,
        "avg_value_3m": None if avg_3 is None else avg_3 * unit_cost,
        "avg_value_6m": None if avg_6 is None else avg_6 * unit_cost,
        "avg_value_12m": None if avg_12 is None else avg_12 * unit_cost,
        "delta_qty_vs_current": expected_stock - current_stock,
        "delta_value_vs_current": expected_value - current_value,
    }


def _row_value(row: dict[str, Any], qty_key: str, value_key: str | None = None) -> float:
    if value_key:
        stored = _to_float(row.get(value_key))
        if stored is not None:
            return stored
    qty = _to_float(row.get(qty_key), 0.0) or 0.0
    cost = _to_float(row.get("unit_cost"), 0.0) or 0.0
    return qty * cost


def _sum_present(rows: list[dict[str, Any]], key: str) -> tuple[float | None, int]:
    total = 0.0
    count = 0
    for row in rows:
        val = _to_float(row.get(key))
        if val is None:
            continue
        total += val
        count += 1
    if not count:
        return None, 0
    return total, count


def summarize_results(
    rows: list[dict[str, Any]],
    requested_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Aggregate stored point estimates for an overview of many items."""
    requested = []
    seen: set[int] = set()
    for raw in requested_ids or []:
        try:
            item_id = int(raw)
        except (TypeError, ValueError):
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        requested.append(item_id)

    lead_qty = 0.0
    lead_value = 0.0
    cycle_qty = 0.0
    cycle_value = 0.0
    safety_qty = 0.0
    safety_value = 0.0
    expected_qty = 0.0
    expected_value = 0.0
    current_qty = 0.0
    current_value = 0.0
    overstock_value = 0.0
    understock_value = 0.0
    items_over = 0
    items_under = 0
    items_on_target = 0

    for row in rows:
        lead_q = _to_float(row.get("forecast_lead_qty"), 0.0) or 0.0
        cycle_q = _to_float(row.get("cycle_stock"), 0.0) or 0.0
        safety_q = _to_float(row.get("safety_stock_used"), 0.0) or 0.0
        expected_q = _to_float(row.get("expected_stock"), 0.0) or 0.0
        current_q = _to_float(row.get("current_stock"), 0.0) or 0.0
        lead_v = _row_value(row, "forecast_lead_qty")
        cycle_v = _row_value(row, "cycle_stock")
        safety_v = _row_value(row, "safety_stock_used")
        expected_v = _row_value(row, "expected_stock", "expected_value")
        current_v = _row_value(row, "current_stock", "current_value")

        lead_qty += lead_q
        lead_value += lead_v
        cycle_qty += cycle_q
        cycle_value += cycle_v
        safety_qty += safety_q
        safety_value += safety_v
        expected_qty += expected_q
        expected_value += expected_v
        current_qty += current_q
        current_value += current_v

        delta = current_v - expected_v
        if delta > 1e-9:
            overstock_value += delta
            items_over += 1
        elif delta < -1e-9:
            understock_value += -delta
            items_under += 1
        else:
            items_on_target += 1

    avg_qty_3m, avg_items_3m = _sum_present(rows, "avg_stock_3m")
    avg_qty_6m, avg_items_6m = _sum_present(rows, "avg_stock_6m")
    avg_qty_12m, avg_items_12m = _sum_present(rows, "avg_stock_12m")
    avg_value_3m, _ = _sum_present(rows, "avg_value_3m")
    avg_value_6m, _ = _sum_present(rows, "avg_value_6m")
    avg_value_12m, _ = _sum_present(rows, "avg_value_12m")

    return {
        "items_requested": len(requested) or len(rows),
        "items_with_roi": len(rows),
        "lead_qty": lead_qty,
        "lead_value": lead_value,
        "cycle_qty": cycle_qty,
        "cycle_value": cycle_value,
        "safety_qty": safety_qty,
        "safety_value": safety_value,
        "expected_qty": expected_qty,
        "expected_value": expected_value,
        "current_qty": current_qty,
        "current_value": current_value,
        "delta_qty": current_qty - expected_qty,
        "delta_value": current_value - expected_value,
        "avg_qty_3m": avg_qty_3m,
        "avg_qty_6m": avg_qty_6m,
        "avg_qty_12m": avg_qty_12m,
        "avg_value_3m": avg_value_3m,
        "avg_value_6m": avg_value_6m,
        "avg_value_12m": avg_value_12m,
        "avg_items_3m": avg_items_3m,
        "avg_items_6m": avg_items_6m,
        "avg_items_12m": avg_items_12m,
        "overstock_value": overstock_value,
        "understock_value": understock_value,
        "items_over": items_over,
        "items_under": items_under,
        "items_on_target": items_on_target,
    }
