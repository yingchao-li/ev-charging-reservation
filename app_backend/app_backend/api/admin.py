from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app_backend.api.dependencies import admin_user
from app_backend.app_backend.utils.database import now_iso
from app_backend.app_backend.utils.schemas import (
    AdminEvent,
    AlertAcknowledge,
    AlertView,
    DemoNoShowRequest,
    DemoOccupancyRequest,
    DemoPlateRequest,
    ReservationView,
)
from app_backend.services.admin import AdminService
from app_backend.services.reservations import ReservationService


router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(admin_user)]
)


def admin_service(request: Request) -> AdminService:
    return AdminService(
        request.app.state.database,
        request.app.state.adapter,
        request.app.state.settings,
    )


def reservation_service(request: Request) -> ReservationService:
    return ReservationService(request.app.state.database, request.app.state.adapter)


@router.get("/overview")
def overview(request: Request):
    return admin_service(request).overview()


@router.get("/bays")
async def bays(request: Request):
    return await admin_service(request).bays()


@router.get("/reservations", response_model=list[ReservationView])
def reservations(
    request: Request,
    status: str | None = None,
    bay_id: str | None = None,
    search: str | None = None,
):
    return admin_service(request).reservations(status, bay_id, search)


@router.get("/events", response_model=list[AdminEvent])
def events(request: Request):
    return admin_service(request).events()


@router.get("/alerts", response_model=list[AlertView])
def alerts(request: Request):
    return admin_service(request).alerts()


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertView)
def acknowledge_alert(alert_id: str, payload: AlertAcknowledge, request: Request):
    return admin_service(request).acknowledge_alert(alert_id, payload.note)


@router.get("/analytics")
def analytics(request: Request):
    return admin_service(request).analytics()


@router.post("/reservations/{reservation_id}/release", response_model=ReservationView)
async def release_reservation(reservation_id: str, request: Request):
    return await reservation_service(request).admin_release(reservation_id)


@router.post("/sync")
async def sync_events(request: Request):
    return await request.app.state.event_sync.sync_once()


@router.post("/demo/plate")
async def demo_plate(payload: DemoPlateRequest, request: Request):
    provider = await request.app.state.adapter.simulate_plate(
        bay_id=payload.bay_id,
        license_plate=payload.license_plate,
        confidence=payload.confidence,
    )
    sync = await request.app.state.event_sync.sync_once()
    return {"provider": provider, "sync": sync}


@router.post("/demo/occupancy")
async def demo_occupancy(payload: DemoOccupancyRequest, request: Request):
    provider = await request.app.state.adapter.simulate_occupancy(
        bay_id=payload.bay_id,
        occupancy=payload.occupancy,
        license_plate=payload.license_plate,
    )
    sync = await request.app.state.event_sync.sync_once()
    return {"provider": provider, "sync": sync}


@router.post("/demo/reservations/{reservation_id}/charging/{action}")
async def demo_charging(reservation_id: str, action: str, request: Request):
    if action not in {"start", "stop"}:
        from app_backend.app_backend.utils.errors import AppError

        raise AppError(422, "INVALID_ACTION", "action must be start or stop")
    reservation = reservation_service(request).get(reservation_id, "", "ADMIN")
    provider = await request.app.state.adapter.simulate_charging(
        reservation["ecurious_reservation_id"], start=action == "start"
    )
    status_value = "CHARGING" if action == "start" else "CHARGE_COMPLETED"
    with request.app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            UPDATE app_reservations
            SET status = ?, simulated_charging = ?, updated_at = ? WHERE id = ?
            """,
            (
                status_value,
                int(action == "start"),
                now_iso(),
                reservation_id,
            ),
        )
    return {"provider": provider, "reservation": reservation_service(request).get(reservation_id, "", "ADMIN")}


@router.post("/demo/no-show")
async def demo_no_show(payload: DemoNoShowRequest, request: Request):
    provider = await request.app.state.adapter.simulate_no_show(payload.now)
    sync = await request.app.state.event_sync.sync_once()
    await reservation_service(request).refresh_active()
    return {"provider": provider, "sync": sync}
