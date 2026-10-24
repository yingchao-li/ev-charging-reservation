from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app_backend.adapters.base import EcuriousAdapter, ProviderError
from app_backend.app_backend.utils.config import Settings
from app_backend.main import create_app


class FakeAdapter(EcuriousAdapter):
    def __init__(self) -> None:
        self.bays = {
            "BAY-001": {
                "bay_id": "BAY-001",
                "reservation_supported": True,
                "occupancy": "free",
                "protection": "released",
                "active_reservation_id": None,
                "last_seen_at": None,
            },
            "BAY-002": {
                "bay_id": "BAY-002",
                "reservation_supported": False,
                "occupancy": "free",
                "protection": "released",
                "active_reservation_id": None,
                "last_seen_at": None,
            },
            "BAY-003": {
                "bay_id": "BAY-003",
                "reservation_supported": True,
                "occupancy": "free",
                "protection": "released",
                "active_reservation_id": None,
                "last_seen_at": None,
            },
        }
        self.reservations: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []
        self.fail_create: ProviderError | None = None

    async def list_bays(self):
        return [
            {
                "bay_id": bay["bay_id"],
                "reservation_supported": bay["reservation_supported"],
                "charger_reference": None,
            }
            for bay in self.bays.values()
        ]

    async def get_bay_status(self, bay_id):
        bay = self.bays.get(bay_id)
        if not bay:
            raise ProviderError(404, "NOT_FOUND", "Bay not found")
        return {key: value for key, value in bay.items() if key != "reservation_supported"}

    async def create_reservation(
        self, *, bay_id, license_plate, start_time, end_time, idempotency_key
    ):
        if self.fail_create:
            raise self.fail_create
        if idempotency_key in self.idempotency:
            return self.reservations[self.idempotency[idempotency_key]]
        if not self.bays[bay_id]["reservation_supported"]:
            raise ProviderError(409, "RESERVATION_NOT_SUPPORTED", "Not supported")
        for reservation in self.reservations.values():
            if (
                reservation["bay_id"] == bay_id
                and reservation["status"] in {"reserved", "checked_in"}
                and start_time < reservation["end_time"]
                and end_time > reservation["start_time"]
            ):
                raise ProviderError(409, "BAY_ALREADY_RESERVED", "Time slot unavailable")
        reservation_id = f"RES-{uuid.uuid4().hex[:8]}"
        reservation = {
            "reservation_id": reservation_id,
            "bay_id": bay_id,
            "license_plate": license_plate,
            "status": "reserved",
            "hardware_status": "protected",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "start_time": start_time,
            "end_time": end_time,
        }
        self.reservations[reservation_id] = reservation
        self.idempotency[idempotency_key] = reservation_id
        self.bays[bay_id]["active_reservation_id"] = reservation_id
        self.bays[bay_id]["protection"] = "protected"
        return reservation

    async def get_reservation(self, reservation_id):
        return self.reservations[reservation_id]

    async def release_reservation(self, reservation_id, reason):
        reservation = self.reservations[reservation_id]
        reservation["status"] = {
            "user_cancelled": "cancelled",
            "no_show": "expired",
        }.get(reason, "released")
        bay = self.bays[reservation["bay_id"]]
        bay["active_reservation_id"] = None
        bay["protection"] = "released"
        return reservation

    async def poll_events(self, after):
        offset = int(after.split("-")[-1]) if after else 0
        events = self.events[offset:]
        cursor = f"cursor-{len(self.events):08d}" if events or after else None
        return {"events": events, "next_cursor": cursor}

    async def simulate_plate(self, *, bay_id, license_plate, confidence):
        reservation = next(
            (
                value
                for value in self.reservations.values()
                if value["bay_id"] == bay_id and value["status"] == "reserved"
            ),
            None,
        )
        matched = bool(reservation and reservation["license_plate"] == license_plate)
        if matched:
            reservation["status"] = "checked_in"
            self.bays[bay_id]["protection"] = "released"
        event = self._event("plate.detected", bay_id, license_plate, confidence)
        return {"event": event, "matched": matched, "outcome": "checked_in" if matched else "plate_mismatch"}

    async def simulate_occupancy(self, *, bay_id, occupancy, license_plate):
        self.bays[bay_id]["occupancy"] = occupancy
        if occupancy == "occupied":
            self.bays[bay_id]["protection"] = "released"
        event = self._event("occupancy.changed", bay_id, None, None)
        return {"event": event, "bay_status": await self.get_bay_status(bay_id)}

    async def simulate_charging(self, reservation_id, *, start):
        return {
            "reservation_id": reservation_id,
            "lifecycle_state": "CHARGING" if start else "CHARGE_COMPLETED",
            "charging": start,
            "public_reservation": self.reservations[reservation_id],
        }

    async def simulate_no_show(self, now):
        expired = []
        for reservation in self.reservations.values():
            if reservation["status"] == "reserved":
                reservation["status"] = "expired"
                self.bays[reservation["bay_id"]]["active_reservation_id"] = None
                self.bays[reservation["bay_id"]]["protection"] = "released"
                self._event("reservation.expired", reservation["bay_id"], None, None)
                expired.append(reservation["reservation_id"])
        return {"expired_reservation_ids": expired, "count": len(expired)}

    def _event(self, event_type, bay_id, plate, confidence):
        event = {
            "event_id": f"EVT-{len(self.events) + 1}",
            "type": event_type,
            "bay_id": bay_id,
            "license_plate": plate,
            "confidence": confidence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.events.append(event)
        return event


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        event_poll_enabled=False,
        station_fixture_path=str(Path(__file__).resolve().parents[2] / "goingelectric_response.json"),
        seed_demo_users=True,
        demo_driver_password="DriverTest123!",
        demo_admin_password="AdminTest123!",
    )


@pytest.fixture
def app(settings, adapter):
    return create_app(settings, adapter)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def driver_headers(client):
    return login(client, "driver@demo.local", "DriverTest123!")


@pytest.fixture
def admin_headers(client):
    return login(client, "admin@demo.local", "AdminTest123!")


@pytest.fixture
def vehicle(client, driver_headers):
    response = client.post(
        "/api/vehicles",
        headers=driver_headers,
        json={"license_plate": "M-AB1234", "nickname": "Demo EV", "is_default": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def reservation_payload(vehicle):
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    return {
        "station_id": "GE-26",
        "bay_id": "BAY-001",
        "vehicle_id": vehicle["id"],
        "start_time": (now - timedelta(minutes=1)).isoformat(),
        "end_time": (now + timedelta(hours=1)).isoformat(),
    }


@pytest.fixture
def reservation(client, driver_headers, reservation_payload):
    response = client.post(
        "/api/reservations",
        headers={**driver_headers, "Idempotency-Key": "test-reservation"},
        json=reservation_payload,
    )
    assert response.status_code == 201, response.text
    return response.json()

