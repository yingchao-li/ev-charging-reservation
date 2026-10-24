from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app_backend.app_backend.utils.errors import AppError, forbidden
from app_backend.services.auth import AuthService


bearer = HTTPBearer(auto_error=False)


def auth_service(request: Request) -> AuthService:
    return AuthService(request.app.state.database, request.app.state.settings)


def raw_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(401, "UNAUTHORIZED", "A valid session token is required")
    return credentials.credentials


def current_user(
    token: Annotated[str, Depends(raw_token)],
    service: Annotated[AuthService, Depends(auth_service)],
) -> sqlite3.Row:
    return service.authenticate(token)


def admin_user(user: Annotated[sqlite3.Row, Depends(current_user)]) -> sqlite3.Row:
    if user["role"] != "ADMIN":
        raise forbidden("Administrator access is required")
    return user

