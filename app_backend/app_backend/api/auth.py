from typing import Annotated

from fastapi import APIRouter, Depends, status

from app_backend.api.dependencies import auth_service, current_user, raw_token
from app_backend.app_backend.utils.schemas import AuthResponse, LoginRequest, Message, RegisterRequest, User
from app_backend.services.auth import AuthService, public_user


router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    service: Annotated[AuthService, Depends(auth_service)],
):
    return service.register(payload)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    service: Annotated[AuthService, Depends(auth_service)],
):
    return service.login(payload)


@router.post("/logout", response_model=Message)
def logout(
    token: Annotated[str, Depends(raw_token)],
    service: Annotated[AuthService, Depends(auth_service)],
):
    service.logout(token)
    return {"message": "Logged out"}


@router.get("/me", response_model=User)
def me(user=Depends(current_user)):
    return public_user(user)

