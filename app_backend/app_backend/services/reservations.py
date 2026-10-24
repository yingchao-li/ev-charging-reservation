from __future__ import annotations

import sqlite3
import uuid
from datetime import timezone
from typing import Any

from app_backend.adapters.base import EcuriousAdapter
from app_backend.app_backend.utils.database import Database, now_iso
from app_backend.app_backend.utils.errors import conflict, forbidden, not_found
from app_backend.app_backend.utils.schemas import ReservationCreate


RESERVATION_SELECT = """
SELECT app_reservations.*, users.email AS user_email,
       vehicles.nickname AS vehicle_nickname, vehicles.license_plate,
       stations.name AS station_name
FROM app_reservations
JOIN users ON users.id = app_reservations.user_id
JOIN vehicles ON vehicles.id = app_reservations.vehicle_id
JOIN stations ON stations.id = app_reservations.station_id
"""


def reservation_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ecurious_reservation_id": row["ecurious_reservation_id"],
        "user_id": row["user_id"],
        "user_email": row["user_email"],
        "vehicle_id": row["vehicle_id"],
        "vehicle_nickname": row["vehicle_nickname"],
        "license_plate": row["license_plate"],
        "station_id": row["station_id"],
        "station_name": row["station_name"],
        "bay_id": row["bay_id"],
        "status": row["status"],
        "ecurious_status": row["ecurious_status"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "attention_message": row["attention_message"],
        "simulated_charging": bool(row["simulated_charging"]),
    }


def app_status_from_provider(provider_status: str, current: str | None = None) -> str:
    if provider_status == "reserved":
        return current if current in {"BLOCKED", "ATTENTION_REQUIRED"} else "RESERVED"
    if provider_status == "checked_in":
        return current if current in {"CHARGING", "COMPLETED"} else "CHECKED_IN"
    return {
        "released": "COMPLETED",
        "expired": "EXPIRED",
        "cancelled": "CANCELLED",
    }.get(provider_status, current or "UNKNOWN")


class ReservationService:
    def __init__(self, database: Database, adapter: EcuriousAdapter) -> None:
        self.database = database
        self.adapter = adapter

    async def create(
        self,
        user_id: str,
        payload: ReservationCreate,
        client_request_key: str,
    ) -> dict[str, Any]:
        key = client_request_key.strip()
        if not key or len(key) > 128:
            raise conflict("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must be 1 to 128 characters")
        with self.database.connect() as connection:
            existing = connection.execute(
                RESERVATION_SELECT
                + " WHERE app_reservations.user_id = ? AND client_request_key = ?",
                (user_id, key),
            ).fetchone()
            if existing is not None:
                return reservation_view(existing)
            vehicle = connection.execute(
                "SELECT * FROM vehicles WHERE id = ? AND user_id = ?",
                (payload.vehicle_id, user_id),
            ).fetchone()
            if vehicle is None:
                raise not_found("Vehicle was not found")
            mapping = connection.execute(
                "SELECT * FROM bay_mappings WHERE bay_id = ? AND station_id = ?",
                (payload.bay_id, payload.station_id),
            ).fetchone()
            if mapping is None:
                raise conflict(
                    "BAY_NOT_MAPPED",
                    "The selected station is not mapped to this Ecurious bay",
                )

        app_id = f"APP-RES-{uuid.uuid4().hex[:12].upper()}"
        provider = await self.adapter.create_reservation(
            bay_id=payload.bay_id,
            license_plate=vehicle["license_plate"],
            start_time=payload.start_time,
            end_time=payload.end_time,
            idempotency_key=app_id,
        )
        timestamp = now_iso()
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO app_reservations(
                        id, ecurious_reservation_id, user_id, vehicle_id, station_id,
                        bay_id, status, ecurious_status, start_time, end_time,
                        created_at, updated_at, provider_confirmed, client_request_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        app_id,
                        provider["reservation_id"],
                        user_id,
                        payload.vehicle_id,
                        payload.station_id,
                        payload.bay_id,
                        app_status_from_provider(provider["status"]),
                        provider["status"],
                        payload.start_time.astimezone(timezone.utc).isoformat(),
                        payload.end_time.astimezone(timezone.utc).isoformat(),
                        timestamp,
                        timestamp,
                        key,
                    ),
                )
                row = connection.execute(
                    RESERVATION_SELECT + " WHERE app_reservations.id = ?", (app_id,)
                ).fetchone()
                assert row is not None
                return reservation_view(row)
        except sqlite3.IntegrityError:
            with self.database.connect() as connection:
                existing = connection.execute(
                    RESERVATION_SELECT
                    + " WHERE app_reservations.user_id = ? AND client_request_key = ?",
                    (user_id, key),
                ).fetchone()
            if existing is not None:
                return reservation_view(existing)
            raise

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                RESERVATION_SELECT
                + " WHERE app_reservations.user_id = ? ORDER BY start_time DESC",
                (user_id,),
            ).fetchall()
        return [reservation_view(row) for row in rows]

    def get(self, reservation_id: str, user_id: str, role: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                RESERVATION_SELECT + " WHERE app_reservations.id = ?", (reservation_id,)
            ).fetchone()
        if row is None:
            raise not_found("Reservation was not found")
        if role != "ADMIN" and row["user_id"] != user_id:
            raise forbidden("Drivers can only access their own reservations")
        return reservation_view(row)

    async def cancel(self, reservation_id: str, user_id: str) -> dict[str, Any]:
        current = self.get(reservation_id, user_id, "DRIVER")
        if current["status"] not in {"RESERVED", "ATTENTION_REQUIRED", "BLOCKED"}:
            raise conflict("CANCELLATION_NOT_ALLOWED", "Reservation can no longer be cancelled")
        provider = await self.adapter.release_reservation(
            current["ecurious_reservation_id"], "user_cancelled"
        )
        return self._update_from_provider(
            reservation_id,
            provider,
            forced_status="CANCELLED",
            cancellation_reason="user_cancelled",
        )

    async def admin_release(self, reservation_id: str) -> dict[str, Any]:
        current = self.get(reservation_id, "", "ADMIN")
        provider = await self.adapter.release_reservation(
            current["ecurious_reservation_id"], "operator_action"
        )
        return self._update_from_provider(reservation_id, provider, forced_status="COMPLETED")

    async def refresh_active(self) -> int:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, ecurious_reservation_id FROM app_reservations
                WHERE status NOT IN ('COMPLETED', 'CANCELLED', 'EXPIRED')
                """
            ).fetchall()
        refreshed = 0
        for row in rows:
            provider = await self.adapter.get_reservation(row["ecurious_reservation_id"])
            self._update_from_provider(row["id"], provider)
            refreshed += 1
        return refreshed

    def _update_from_provider(
        self,
        reservation_id: str,
        provider: dict[str, Any],
        *,
        forced_status: str | None = None,
        cancellation_reason: str | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT status FROM app_reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
            if current is None:
                raise not_found("Reservation was not found")
            status = forced_status or app_status_from_provider(
                provider["status"], current["status"]
            )
            connection.execute(
                """
                UPDATE app_reservations
                SET status = ?, ecurious_status = ?, updated_at = ?,
                    cancellation_reason = COALESCE(?, cancellation_reason),
                    attention_message = CASE WHEN ? IN ('CHECKED_IN','CHARGING','COMPLETED')
                                             THEN NULL ELSE attention_message END
                WHERE id = ?
                """,
                (
                    status,
                    provider["status"],
                    now_iso(),
                    cancellation_reason,
                    status,
                    reservation_id,
                ),
            )
            row = connection.execute(
                RESERVATION_SELECT + " WHERE app_reservations.id = ?", (reservation_id,)
            ).fetchone()
            assert row is not None
            return reservation_view(row)

