from app.m7_access import assert_create_user_allowed, databases_visible_to, user_may_open_database


DATABASES = [
    {"name": "consumables", "display_name": "consumables", "product": ""},
    {"name": "demo", "display_name": "smart_stock", "product": ""},
    {"name": "drilling", "display_name": "drilling", "product": ""},
    {"name": "goa", "display_name": "Goa", "product": "m7"},
]


def test_platform_admin_sees_every_database():
    visible = databases_visible_to({"is_admin": True, "database_name": "consumables"}, DATABASES)
    assert [row["name"] for row in visible] == ["consumables", "demo", "drilling", "goa"]


def test_m7_admin_sees_only_m7_companies():
    visible = databases_visible_to({"is_admin": False, "is_m7_admin": True, "database_name": "goa"}, DATABASES)
    assert [row["name"] for row in visible] == ["goa"]


def test_company_user_sees_only_their_database():
    visible = databases_visible_to({"is_admin": False, "is_m7_admin": False, "database_name": "goa"}, DATABASES)
    assert [row["name"] for row in visible] == ["goa"]


def test_m7_admin_cannot_open_live_databases():
    actor = {"is_admin": False, "is_m7_admin": True, "database_name": "goa"}
    assert user_may_open_database(actor, "goa", "m7") is True
    assert user_may_open_database(actor, "consumables", "") is False
    assert user_may_open_database(actor, "drilling", "") is False
    assert user_may_open_database(actor, "demo", "") is False


def test_m7_admin_creates_company_users_only():
    actor = {"is_admin": False, "is_m7_admin": True}
    assert_create_user_allowed(actor, product="m7", is_admin=False, is_m7_admin=False)
    for product in ("", "purchasing"):
        try:
            assert_create_user_allowed(actor, product=product, is_admin=False, is_m7_admin=False)
        except ValueError as exc:
            assert "M7 companies" in str(exc)
        else:
            raise AssertionError(product)
    try:
        assert_create_user_allowed(actor, product="m7", is_admin=True, is_m7_admin=False)
    except ValueError as exc:
        assert "cannot grant" in str(exc)
    else:
        raise AssertionError("platform flag")


def test_company_user_cannot_create_users():
    try:
        assert_create_user_allowed(
            {"is_admin": False, "is_m7_admin": False},
            product="m7",
            is_admin=False,
            is_m7_admin=False,
        )
    except ValueError as exc:
        assert str(exc) == "Admin access required"
    else:
        raise AssertionError("company user")
