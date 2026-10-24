from typing import Annotated

from fastapi import APIRouter, Query, Request

from app_backend.app_backend.utils.schemas import BayAvailability, StationDetail, StationSummary
from app_backend.services.stations import StationService


router = APIRouter(prefix="/api", tags=["stations"])


def service(request: Request) -> StationService:
    return StationService(request.app.state.database, request.app.state.adapter)


@router.get("/stations", response_model=list[StationSummary])
async def list_stations(
    request: Request,
    search: str | None = None,
    connector: str | None = None,
    min_power: Annotated[float, Query(ge=0)] = 0,
    network: str | None = None,
    ne_lat: float | None = None,
    ne_lng: float | None = None,
    sw_lat: float | None = None,
    sw_lng: float | None = None,
):
    return await service(request).list(
        search=search,
        connector=connector,
        min_power=min_power,
        network=network,
        ne_lat=ne_lat,
        ne_lng=ne_lng,
        sw_lat=sw_lat,
        sw_lng=sw_lng,
    )


@router.get("/stations/{station_id}", response_model=StationDetail)
async def get_station(station_id: str, request: Request):
    return await service(request).get(station_id)


@router.get("/bays/{bay_id}/availability", response_model=BayAvailability)
async def get_bay_availability(bay_id: str, request: Request):
    return await service(request).availability(bay_id)

