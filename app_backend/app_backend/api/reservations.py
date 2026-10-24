from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status

from app_backend.api.dependencies import current_user
from app_backend.app_backend.utils.schemas import ReservationCreate, ReservationView
from app_backend.services.reservations import ReservationService


router = APIRouter(prefix="/api/reservations", tags=["reservations"])


def service(request: Request) -> ReservationService:
    return ReservationService(request.app.state.database, request.app.state.adapter)


@router.post("", response_model=ReservationView, status_code=status.HTTP_201_CREATED)
async def create_reservation(
    payload: ReservationCreate,
    request: Request,
    user=Depends(current_user),
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key") ] = "",
):
    return await service(request).create(user["id"], payload, idempotency_key)


@router.get("/my", response_model=list[ReservationView])
def my_reservations(request: Request, user=Depends(current_user)):
    return service(request).list_for_user(user["id"])


@router.get("/{reservation_id}", response_model=ReservationView)
def get_reservation(reservation_id: str, request: Request, user=Depends(current_user)):
    return service(request).get(reservation_id, user["id"], user["role"])


@router.post("/{reservation_id}/cancel", response_model=ReservationView)
async def cancel_reservation(
    reservation_id: str, request: Request, user=Depends(current_user)
):
    return await service(request).cancel(reservation_id, user["id"])

