from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app_backend.adapters.base import EcuriousAdapter
from app_backend.app_backend.utils.config import Settings
from app_backend.app_backend.utils.database import Database, now_iso
from app_backend.app_backend.utils.security import mask_plate
from app_backend.services.reservations import ReservationService


logger = logging.getLogger("ecurious_app.event_sync")


class EventSyncService:
    def __init__(
        self, database: Database, adapter: EcuriousAdapter, settings: Settings
    ) -> None:
        self.database = database
        self.adapter = adapter
        self.settings = settings
        self.owner = f"sync-{uuid.uuid4().hex}"
        self.reservations = ReservationService(database, adapter)

    async def sync_once(self) -> dict[str, Any]:
        cursor = self._acquire_lease()
        if cursor is False:
            return {"processed": 0, "cursor": None, "lease_acquired": False}
        try:
            page = await self.adapter.poll_events(cursor or None)
            processed = 0
            for event in page.get("events", []):
                if await self._process(event):
                    processed += 1
            next_cursor = page.get("next_cursor") or cursor
            self._save_cursor_and_release(next_cursor)
            return {
                "processed": processed,
                "cursor": next_cursor,
                "lease_acquired": True,
            }
        except Exception:
            self._release_lease()
            raise

    def _acquire_lease(self) -> str | bool:
        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=self.settings.sync_lease_seconds)
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM sync_state WHERE key = 'ecurious_events'"
            ).fetchone()
            if row and row["lease_until"]:
                current_lease = datetime.fromisoformat(row["lease_until"])
                if current_lease > now and row["lease_owner"] != self.owner:
                    return False
            if row:
                cursor = row["value"] or ""
                connection.execute(
                    """
                    UPDATE sync_state SET lease_owner = ?, lease_until = ?, updated_at = ?
                    WHERE key = 'ecurious_events'
                    """,
                    (self.owner, lease_until.isoformat(), now.isoformat()),
                )
            else:
                cursor = ""
                connection.execute(
                    """
                    INSERT INTO sync_state(key, value, lease_owner, lease_until, updated_at)
                    VALUES ('ecurious_events', '', ?, ?, ?)
                    """,
                    (self.owner, lease_until.isoformat(), now.isoformat()),
                )
            return cursor

    def _save_cursor_and_release(self, cursor: str | None) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE sync_state SET value = ?, lease_owner = NULL, lease_until = NULL,
                    updated_at = ?
                WHERE key = 'ecurious_events' AND lease_owner = ?
                """,
                (cursor or "", now_iso(), self.owner),
            )

    def _release_lease(self) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE sync_state SET lease_owner = NULL, lease_until = NULL, updated_at = ?
                WHERE key = 'ecurious_events' AND lease_owner = ?
                """,
                (now_iso(), self.owner),
            )

    async def _process(self, event: dict[str, Any]) -> bool:
        with self.database.connect() as connection:
            duplicate = connection.execute(
                "SELECT 1 FROM processed_events WHERE external_event_id = ?",
                (event["event_id"],),
            ).fetchone()
            reservation = connection.execute(
                """
                SELECT app_reservations.*, vehicles.license_plate
                FROM app_reservations
                JOIN vehicles ON vehicles.id = app_reservations.vehicle_id
                WHERE bay_id = ?
                  AND status NOT IN ('COMPLETED', 'CANCELLED', 'EXPIRED')
                ORDER BY start_time, created_at LIMIT 1
                """,
                (event["bay_id"],),
            ).fetchone()
        if duplicate:
            return False

        event_type = event["type"]
        result = "RECORDED"
        reservation_id = reservation["id"] if reservation else None
        if event_type == "plate.detected":
            result = await self._process_plate(event, reservation)
        elif event_type == "occupancy.changed":
            result = await self._process_occupancy(event, reservation)
        elif event_type == "reservation.expired":
            result = await self._process_expiry(event, reservation)

        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO processed_events(
                    external_event_id, event_type, bay_id, reservation_id,
                    masked_plate, confidence, processing_result, timestamp, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event_type,
                    event["bay_id"],
                    reservation_id,
                    mask_plate(event.get("license_plate")),
                    event.get("confidence"),
                    result,
                    event["timestamp"],
                    now_iso(),
                ),
            )
        return True

    async def _process_plate(self, event: dict[str, Any], reservation) -> str:
        if reservation is None:
            return "NO_ACTIVE_RESERVATION"
        if event.get("license_plate") != reservation["license_plate"]:
            self._create_alert(
                event["event_id"],
                event["bay_id"],
                reservation["id"],
                "WRONG_PLATE",
                "A plate different from the reserved vehicle was detected. Occupancy is not yet confirmed.",
            )
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE app_reservations
                    SET status = 'ATTENTION_REQUIRED', attention_message = ?, updated_at = ?
                    WHERE id = ? AND status = 'RESERVED'
                    """,
                    ("Wrong plate detected; occupancy is not confirmed", now_iso(), reservation["id"]),
                )
            return "PLATE_MISMATCH"

        provider = await self.adapter.get_reservation(reservation["ecurious_reservation_id"])
        self.reservations._update_from_provider(reservation["id"], provider)
        return "CHECK_IN_SUCCESSFUL" if provider["status"] == "checked_in" else "PLATE_MATCHED"

    async def _process_occupancy(self, event: dict[str, Any], reservation) -> str:
        status = await self.adapter.get_bay_status(event["bay_id"])
        if reservation is None:
            return f"OCCUPANCY_{status['occupancy'].upper()}_NO_RESERVATION"
        if status["occupancy"] == "occupied" and reservation["status"] in {
            "RESERVED",
            "ATTENTION_REQUIRED",
        }:
            self._create_alert(
                event["event_id"],
                event["bay_id"],
                reservation["id"],
                "BLOCKED_BAY",
                "A reserved bay is physically occupied without a successful check-in.",
            )
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE app_reservations
                    SET status = 'BLOCKED', attention_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("Reserved bay is occupied by an unauthorised or unconfirmed vehicle", now_iso(), reservation["id"]),
                )
            return "BLOCKED_RESERVED_BAY"
        if status["occupancy"] == "free":
            self._resolve_alerts(reservation["id"], "BLOCKED_BAY")
        return f"OCCUPANCY_{status['occupancy'].upper()}"

    async def _process_expiry(self, event: dict[str, Any], reservation) -> str:
        if reservation is None:
            return "EXPIRED_WITHOUT_LOCAL_MATCH"
        provider = await self.adapter.get_reservation(reservation["ecurious_reservation_id"])
        self.reservations._update_from_provider(reservation["id"], provider)
        return "NO_SHOW_EXPIRED" if provider["status"] == "expired" else "EXPIRY_RECONCILED"

    def _create_alert(
        self,
        source_event_id: str,
        bay_id: str,
        reservation_id: str | None,
        alert_type: str,
        message: str,
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO alerts(
                    id, bay_id, reservation_id, source_event_id, alert_type,
                    status, message, created_at
                ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?)
                """,
                (
                    f"ALT-{uuid.uuid4().hex[:12].upper()}",
                    bay_id,
                    reservation_id,
                    source_event_id,
                    alert_type,
                    message,
                    now_iso(),
                ),
            )

    def _resolve_alerts(self, reservation_id: str, alert_type: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE alerts SET status = 'RESOLVED', resolved_at = ?
                WHERE reservation_id = ? AND alert_type = ? AND status != 'RESOLVED'
                """,
                (now_iso(), reservation_id, alert_type),
            )

