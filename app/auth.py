from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
import mysql.connector

from app import db

JWT_SECRET = os.getenv("JWT_SECRET", "nostradamus-secret-key")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24 * 7  # 1 week


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
        conn.commit()
    finally:
        conn.close()


def register_user(username: str, email: str, password: str, database_name: str, is_admin: bool = False) -> dict:
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
                "SELECT id, username, email, database_name, is_admin, created_at FROM users ORDER BY id"
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        {**row, "is_admin": bool(row["is_admin"]), "created_at": str(row["created_at"])}
        for row in rows
    ]


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
