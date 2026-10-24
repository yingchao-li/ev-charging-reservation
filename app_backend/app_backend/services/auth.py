from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app_backend.app_backend.utils.config import Settings
from app_backend.app_backend.utils.database import Database, now_iso
from app_backend.app_backend.utils.errors import AppError, conflict
from app_backend.app_backend.utils.schemas import LoginRequest, RegisterRequest
from app_backend.app_backend.utils.security import hash_password, new_session_token, token_hash, verify_password


def public_user(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


class AuthService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def register(self, payload: RegisterRequest) -> dict[str, Any]:
        user_id = f"USR-{uuid.uuid4().hex[:12].upper()}"
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO users(id, email, password_hash, role, created_at)
                    VALUES (?, ?, ?, 'DRIVER', ?)
                    """,
                    (user_id, payload.email, hash_password(payload.password), now_iso()),
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                assert row is not None
                return self._create_session(connection, row)
        except sqlite3.IntegrityError as exc:
            raise conflict("EMAIL_ALREADY_REGISTERED", "Email is already registered") from exc

    def login(self, payload: LoginRequest) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (payload.email,)
            ).fetchone()
            if row is None or not verify_password(payload.password, row["password_hash"]):
                raise AppError(401, "INVALID_CREDENTIALS", "Invalid email or password")
            return self._create_session(connection, row)

    def _create_session(
        self, connection: sqlite3.Connection, user: sqlite3.Row
    ) -> dict[str, Any]:
        raw_token = new_session_token()
        now = datetime.now(timezone.utc)
        connection.execute(
            """
            INSERT INTO sessions(token_hash, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                token_hash(raw_token),
                user["id"],
                (now + timedelta(hours=self.settings.session_hours)).isoformat(),
                now.isoformat(),
            ),
        )
        return {"access_token": raw_token, "token_type": "bearer", "user": public_user(user)}

    def authenticate(self, raw_token: str) -> sqlite3.Row:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT users.* FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.revoked_at IS NULL
                  AND sessions.expires_at > ?
                """,
                (token_hash(raw_token), now_iso()),
            ).fetchone()
        if row is None:
            raise AppError(401, "UNAUTHORIZED", "A valid session token is required")
        return row

    def logout(self, raw_token: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                (now_iso(), token_hash(raw_token)),
            )

