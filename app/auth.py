from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
import mysql.connector

from app import db

logger = logging.getLogger(__name__)
_DEFAULT_JWT_SECRET = "nostradamus-secret-key"
JWT_SECRET = os.getenv("JWT_SECRET", _DEFAULT_JWT_SECRET)

if JWT_SECRET == _DEFAULT_JWT_SECRET:
    logger.warning("JWT_SECRET not set — using insecure default; set JWT_SECRET in production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


def _master_conn():
    return mysql.connector.connect(
        host=os.getenv("MASTER_DB_HOST", "raspberrypi.local"),
        port=int(os.getenv("MASTER_DB_PORT", "4406")),
        user=os.getenv("MASTER_DB_USER", "root"),
        password=os.getenv("MASTER_DB_PASSWORD", "Superman"),
        database="nostradamus_master",
    )


def ensure_users_table() -> None:
    conn = _master_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(100) NOT NULL,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    database_name VARCHAR(100) NOT NULL,
                    is_admin TINYINT(1) NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = 'nostradamus_master' AND table_name = 'users' AND column_name = 'is_admin'"
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN is_admin TINYINT(1) NOT NULL DEFAULT 0"
                )
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = 'nostradamus_master' AND table_name = 'users' AND column_name = 'last_seen_at'"
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE users ADD COLUMN last_seen_at DATETIME NULL")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS db_ui_config (
                    db_name VARCHAR(100) PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_ui_config (
                    user_id INT NOT NULL,
                    db_name VARCHAR(100) NOT NULL,
                    config_json TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, db_name)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS login_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NULL,
                    email VARCHAR(255) NOT NULL,
                    success TINYINT(1) NOT NULL DEFAULT 1,
                    ip VARCHAR(64) NULL,
                    user_agent VARCHAR(255) NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_login_history_user (user_id, created_at),
                    INDEX idx_login_history_email (email, created_at)
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def register_user(username: str, email: str, password: str, database_name: str, is_admin: bool = False) -> dict:
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters")
    # verify database exists and is active
    db._get_db_config(database_name)

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    conn = _master_conn()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cursor.fetchone():
                raise ValueError("Email already registered")

            cursor.execute(
                "INSERT INTO users (username, email, password_hash, database_name, is_admin) VALUES (%s, %s, %s, %s, %s)",
                (username, email, password_hash, database_name, int(is_admin)),
            )
            conn.commit()
            user_id = cursor.lastrowid

    finally:
        conn.close()

    return {"id": user_id, "username": username, "email": email, "database_name": database_name, "is_admin": is_admin}


def login_user(email: str, password: str) -> dict:
    conn = _master_conn()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT id, username, email, password_hash, database_name, is_admin FROM users WHERE email = %s",
                (email,),
            )
            user = cursor.fetchone()
    finally:
        conn.close()

    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise ValueError("Invalid email or password")

    payload = {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "database_name": user["database_name"],
        "is_admin": bool(user["is_admin"]),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "database_name": user["database_name"],
            "is_admin": bool(user["is_admin"]),
        },
    }


def _fmt_dt(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def record_login(
    *,
    email: str,
    success: bool,
    ip: str = "",
    user_agent: str = "",
    user_id: int | None = None,
) -> None:
    email = (email or "").strip()
    if not email:
        return
    resolved_id = user_id
    conn = _master_conn()
    try:
        with conn.cursor() as cursor:
            if resolved_id is None:
                cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
                row = cursor.fetchone()
                resolved_id = row[0] if row else None
            cursor.execute(
                """
                INSERT INTO login_history (user_id, email, success, ip, user_agent, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    resolved_id,
                    email,
                    int(bool(success)),
                    (ip or "")[:64] or None,
                    (user_agent or "")[:255] or None,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )
        conn.commit()
    except Exception:
        logger.exception("failed to record login history")
        return
    finally:
        conn.close()
    if success and resolved_id:
        touch_last_seen(resolved_id, force=True)


def touch_last_seen(user_id: int, *, force: bool = False) -> None:
    if not user_id:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = _master_conn()
    try:
        with conn.cursor() as cursor:
            if force:
                cursor.execute(
                    "UPDATE users SET last_seen_at = %s WHERE id = %s",
                    (now, user_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE users
                    SET last_seen_at = %s
                    WHERE id = %s
                      AND (last_seen_at IS NULL OR last_seen_at < %s)
                    """,
                    (now, user_id, now - timedelta(minutes=5)),
                )
        conn.commit()
    except Exception:
        logger.exception("failed to update last_seen_at")
    finally:
        conn.close()


def list_login_history(user_id: int, limit: int = 100) -> list[dict]:
    conn = _master_conn()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT id, user_id, email, success, ip, user_agent, created_at
                FROM login_history
                WHERE user_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (user_id, max(1, min(int(limit), 500))),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "email": row["email"],
            "success": bool(row["success"]),
            "ip": row["ip"],
            "user_agent": row["user_agent"],
            "created_at": _fmt_dt(row["created_at"]),
        }
        for row in rows
    ]


def require_admin(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ValueError("Token expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token")
    if not payload.get("is_admin"):
        raise ValueError("Admin access required")
    return payload


def list_users() -> list[dict]:
    conn = _master_conn()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.email,
                    u.database_name,
                    u.is_admin,
                    u.created_at,
                    u.last_seen_at,
                    (
                        SELECT MAX(h.created_at)
                        FROM login_history h
                        WHERE h.user_id = u.id AND h.success = 1
                    ) AS last_login_at
                FROM users u
                ORDER BY u.id
                """
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {
            **row,
            "is_admin": bool(row["is_admin"]),
            "created_at": _fmt_dt(row["created_at"]),
            "last_login_at": _fmt_dt(row["last_login_at"]),
            "last_seen_at": _fmt_dt(row.get("last_seen_at")),
        }
        for row in rows
    ]


def list_users_for_database(database_name: str) -> list[dict]:
    db_name = (database_name or "").strip()
    if not db_name:
        raise ValueError("database_name is required")
    conn = _master_conn()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                """
                SELECT id, username, email
                FROM users
                WHERE database_name = %s
                ORDER BY username, id
                """,
                (db_name,),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return rows


def require_db_users_access(token: str, database_name: str) -> dict:
    payload = verify_token(token)
    db_name = (database_name or "").strip()
    if not db_name:
        raise ValueError("db is required")
    if not payload.get("is_admin") and payload.get("database_name") != db_name:
        raise ValueError("Access denied for this database")
    return payload


def delete_user(user_id: int) -> bool:
    conn = _master_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit()
            return cursor.rowcount > 0
    finally:
        conn.close()


def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ValueError("Token expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token")
    return payload


def get_db_ui_config(db_name: str) -> dict:
    import json
    conn = _master_conn()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("SELECT config_json FROM db_ui_config WHERE db_name = %s", (db_name,))
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    return json.loads(row["config_json"])


def set_db_ui_config(db_name: str, config: dict) -> None:
    import json
    conn = _master_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO db_ui_config (db_name, config_json) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE config_json = VALUES(config_json)",
                (db_name, json.dumps(config)),
            )
        conn.commit()
    finally:
        conn.close()


def get_user_ui_config(user_id: int, db_name: str) -> dict:
    import json
    conn = _master_conn()
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT config_json FROM user_ui_config WHERE user_id = %s AND db_name = %s",
                (user_id, db_name),
            )
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    return json.loads(row["config_json"])


def set_user_ui_config(user_id: int, db_name: str, config: dict) -> None:
    import json
    conn = _master_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_ui_config (user_id, db_name, config_json) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE config_json = VALUES(config_json)",
                (user_id, db_name, json.dumps(config)),
            )
        conn.commit()
    finally:
        conn.close()
