from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PLATE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9 -]{1,19}$")


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone offset")
    return value


def normalized_plate(value: str) -> str:
    value = " ".join(value.strip().upper().split())
    if not PLATE_PATTERN.fullmatch(value):
        raise ValueError("license_plate contains unsupported characters")
    return value


class Role(StrEnum):
    DRIVER = "DRIVER"
    ADMIN = "ADMIN"


class User(BaseModel):
    id: str
    email: str
    role: Role
    created_at: datetime


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("invalid email address")
        return value


class LoginRequest(RegisterRequest):
    pass


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: User


class Message(BaseModel):
    message: str


class VehicleCreate(BaseModel):
    license_plate: str = Field(min_length=2, max_length=20)
    nickname: str = Field(min_length=1, max_length=80)
    is_default: bool = False

    @field_validator("license_plate")
    @classmethod
    def validate_plate(cls, value: str) -> str:
        return normalized_plate(value)


class VehicleUpdate(BaseModel):
    license_plate: str | None = Field(default=None, min_length=2, max_length=20)
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    is_default: bool | None = None

    @field_validator("license_plate")
    @classmethod
    def validate_plate(cls, value: str | None) -> str | None:
        return normalized_plate(value) if value is not None else None


class Vehicle(BaseModel):
    id: str
    license_plate: str
    nickname: str
    is_default: bool
    created_at: datetime


class Chargepoint(BaseModel):
    type: str
    power: float
    count: int


class StationSummary(BaseModel):
    id: str
    external_station_id: str
    source: str
    name: str
    city: str
    postcode: str
    street: str
    lat: float
    lng: float
    network: str | None = None
    verified: bool
    fault: bool
    chargepoints: list[Chargepoint]
    source_url: str
    ecurious_bay_ids: list[str]
    reservation_supported: bool
    availability_label: str


class StationDetail(StationSummary):
    country: str


class BayAvailability(BaseModel):
    bay_id: str
    station_id: str
    reservation_supported: bool
    occupancy: Literal["free", "occupied", "unknown"]
    protection: Literal["protected", "released", "unknown"]
    active_reservation_id: str | None = None
    last_seen_at: datetime | None = None
    availability_label: str


class ReservationCreate(BaseModel):
    station_id: str
    bay_id: str
    vehicle_id: str
    start_time: datetime
    end_time: datetime

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        return aware(value)

    @model_validator(mode="after")
    def validate_interval(self) -> "ReservationCreate":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be earlier than end_time")
        return self


class ReservationView(BaseModel):
    id: str
    ecurious_reservation_id: str
    user_id: str
    user_email: str
    vehicle_id: str
    vehicle_nickname: str
    license_plate: str
    station_id: str
    station_name: str
    bay_id: str
    status: str
    ecurious_status: str
    start_time: datetime
    end_time: datetime
    created_at: datetime
    updated_at: datetime
    attention_message: str | None = None
    simulated_charging: bool = False


class AdminEvent(BaseModel):
    external_event_id: str
    event_type: str
    bay_id: str
    reservation_id: str | None = None
    masked_plate: str | None = None
    confidence: float | None = None
    processing_result: str
    timestamp: datetime


class AlertView(BaseModel):
    id: str
    bay_id: str
    reservation_id: str | None = None
    alert_type: str
    status: str
    message: str
    created_at: datetime
    resolved_at: datetime | None = None


class AlertAcknowledge(BaseModel):
    note: str = Field(default="Acknowledged by operator", max_length=300)


class DemoPlateRequest(BaseModel):
    bay_id: str
    license_plate: str = Field(min_length=2, max_length=20)
    confidence: Annotated[float, Field(ge=0, le=1)] = 0.97

    @field_validator("license_plate")
    @classmethod
    def validate_plate(cls, value: str) -> str:
        return normalized_plate(value)


class DemoOccupancyRequest(BaseModel):
    bay_id: str
    occupancy: Literal["free", "occupied", "unknown"]
    license_plate: str | None = None

    @field_validator("license_plate")
    @classmethod
    def validate_plate(cls, value: str | None) -> str | None:
        return normalized_plate(value) if value is not None else None


class DemoNoShowRequest(BaseModel):
    now: datetime | None = None

    @field_validator("now")
    @classmethod
    def validate_now(cls, value: datetime | None) -> datetime | None:
        return aware(value) if value is not None else None

