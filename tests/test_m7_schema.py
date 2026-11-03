import pathlib
import subprocess
import sys

import pytest

from app.m7_schema import (
    CREATE_TABLES,
    LiveDatabaseRefused,
    assert_m7_database_allowed,
    normalize_m7_forecast_rows,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "add_m7_forecast_tables.py"


@pytest.mark.parametrize("name", ["drilling", "smart_stock", "consumables", "Consumables", "demo"])
def test_live_purchasing_databases_are_refused(name):
    with pytest.raises(LiveDatabaseRefused):
        assert_m7_database_allowed(name)


def test_new_customer_name_is_allowed():
    assert assert_m7_database_allowed("goa") == "goa"


def test_statements_only_create_new_tables():
    joined = "\n".join(CREATE_TABLES.values()).lower()
    assert "alter table" not in joined
    assert "drop table" not in joined
    for name, statement in CREATE_TABLES.items():
        assert f"create table if not exists {name}" in statement.lower()
    assert "create table if not exists items" not in joined
    assert "create table if not exists item_histories" not in joined


def test_script_refuses_live_database_without_connecting():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "consumables"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "live purchasing database" in completed.stderr


def test_script_refuses_all_mode():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "Refusing --all" in completed.stderr


def test_normalize_m7_forecast_rows_accepts_valid_months():
    prepared, items = normalize_m7_forecast_rows(
        [
            {
                "item_number": "10084",
                "forecast_year": 2026,
                "forecast_month": 11,
                "qty": 12.5,
                "model": "SeasonalNaive",
                "run_id": "abc",
            },
            {
                "item_number": "10084",
                "forecast_year": 2026,
                "forecast_month": 12,
                "qty": 10,
                "model": "SeasonalNaive",
                "run_id": "abc",
            },
            {
                "item_number": "10093",
                "forecast_year": 2026,
                "forecast_month": 11,
                "qty": 1,
                "model": "Naive",
                "run_id": "abc",
            },
        ]
    )
    assert items == ["10084", "10093"]
    assert len(prepared) == 3
    assert prepared[0][0] == "10084"
    assert prepared[0][3] == 12.5


def test_normalize_m7_forecast_rows_rejects_bad_month():
    with pytest.raises(ValueError, match="Bad forecast month"):
        normalize_m7_forecast_rows(
            [{"item_number": "1", "forecast_year": 2026, "forecast_month": 13, "qty": 1}]
        )
