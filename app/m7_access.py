"""Who may see an M7 company, without opening a database connection."""

M7_PRODUCT = "m7"


def databases_visible_to(user: dict, databases: list[dict]) -> list[dict]:
    if user.get("is_admin"):
        return list(databases)
    if user.get("is_m7_admin"):
        return [row for row in databases if (row.get("product") or "") == M7_PRODUCT]
    allowed = user.get("database_name")
    return [row for row in databases if row.get("name") == allowed]


def user_may_open_database(user: dict, database_name: str, product: str | None) -> bool:
    if user.get("is_admin"):
        return True
    if user.get("is_m7_admin"):
        return (product or "") == M7_PRODUCT
    return user.get("database_name") == database_name


def assert_create_user_allowed(actor: dict, *, product: str, is_admin: bool, is_m7_admin: bool) -> None:
    if actor.get("is_admin"):
        return
    if not actor.get("is_m7_admin"):
        raise ValueError("Admin access required")
    if is_admin or is_m7_admin:
        raise ValueError("M7 admin cannot grant admin access")
    if (product or "") != M7_PRODUCT:
        raise ValueError("M7 admin can only create users for M7 companies")
