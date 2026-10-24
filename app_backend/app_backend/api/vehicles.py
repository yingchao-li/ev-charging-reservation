from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app_backend.api.dependencies import current_user
from app_backend.app_backend.utils.schemas import Message, Vehicle, VehicleCreate, VehicleUpdate
from app_backend.services.vehicles import VehicleService


router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])


def service(request: Request) -> VehicleService:
    return VehicleService(request.app.state.database)


@router.get("", response_model=list[Vehicle])
def list_vehicles(request: Request, user=Depends(current_user)):
    return service(request).list(user["id"])


@router.post("", response_model=Vehicle, status_code=status.HTTP_201_CREATED)
def create_vehicle(payload: VehicleCreate, request: Request, user=Depends(current_user)):
    return service(request).create(user["id"], payload)


@router.patch("/{vehicle_id}", response_model=Vehicle)
def update_vehicle(
    vehicle_id: str, payload: VehicleUpdate, request: Request, user=Depends(current_user)
):
    return service(request).update(user["id"], vehicle_id, payload)


@router.delete("/{vehicle_id}", response_model=Message)
def delete_vehicle(vehicle_id: str, request: Request, user=Depends(current_user)):
    service(request).delete(user["id"], vehicle_id)
    return {"message": "Vehicle deleted"}

