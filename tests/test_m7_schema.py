import pathlib
import subprocess
import sys

import pytest

from app.m7_schema import CREATE_TABLES, LiveDatabaseRefused, assert_m7_database_allowed

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
