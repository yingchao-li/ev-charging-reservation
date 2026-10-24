from __future__ import annotations

import sqlite3
from typing import Any

from app_backend.adapters.base import EcuriousAdapter
from app_backend.app_backend.utils.config import Settings
from app_backend.app_backend.utils.database import Database, now_iso
from app_backend.app_backend.utils.errors import not_found
from app_backend.app_backend.utils.security import mask_plate
from app_backend.services.reservations import RESERVATION_SELECT, reservation_view
from app_backend.services.stations import availability_label


def alert_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "bay_id": row["bay_id"],
        "reservation_id": row["reservation_id"],
        "alert_type": row["alert_type"],
        "status": row["status"],
        "message": row["message"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
    }


class AdminService:
    def __init__(
        self, database: Database, adapter: EcuriousAdapter, settings: Settings
    ) -> None:
        self.database = database
        self.adapter = adapter
        self.settings = settings

    def overview(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM app_reservations").fetchone()[0]
            active = connection.execute(
                """SELECT COUNT(*) FROM app_reservations
                   WHERE status NOT IN ('COMPLETED','CANCELLED','EXPIRED')"""
            ).fetchone()[0]
            checked_in = connection.execute(
                "SELECT COUNT(*) FROM app_reservations WHERE status IN ('CHECKED_IN','CHARGING','CHARGE_COMPLETED')"
            ).fetchone()[0]
            no_shows = connection.execute(
                "SELECT COUNT(*) FROM app_reservations WHERE status = 'EXPIRED'"
            ).fetchone()[0]
            blocked = connection.execute(
                "SELECT COUNT(*) FROM alerts WHERE alert_type = 'BLOCKED_BAY' AND status = 'OPEN'"
            ).fetchone()[0]
            completed = connection.execute(
                "SELECT COUNT(*) FROM app_reservations WHERE status = 'COMPLETED'"
            ).fetchone()[0]
        return {
            "total_reservations": total,
            "active_reservations": active,
            "checked_in_vehicles": checked_in,
            "no_show_count": no_shows,
            "blocked_bay_alerts": blocked,
            "completion_rate": round((completed / total * 100) if total else 0, 1),
        }

    async def bays(self) -> list[dict[str, Any]]:
        bays = await self.adapter.list_bays()
        output = []
        for bay in bays:
            status = await self.adapter.get_bay_status(bay["bay_id"])
            with self.database.connect() as connection:
                context = connection.execute(
                    """
                    SELECT stations.name AS station_name, app_reservations.id AS app_reservation_id,
                           app_reservations.status AS app_status,
                           EXISTS(SELECT 1 FROM alerts WHERE alerts.bay_id = bay_mappings.bay_id
                                  AND alerts.alert_type = 'BLOCKED_BAY' AND alerts.status = 'OPEN') AS blocked
                    FROM bay_mappings
                    JOIN stations ON stations.id = bay_mappings.station_id
                    LEFT JOIN app_reservations ON app_reservations.ecurious_reservation_id = ?
                    WHERE bay_mappings.bay_id = ?
                    """,
                    (status.get("active_reservation_id"), bay["bay_id"]),
                ).fetchone()
            output.append(
                {
                    **bay,
                    **status,
                    "station_name": context["station_name"] if context else None,
                    "app_reservation_id": context["app_reservation_id"] if context else None,
                    "app_status": context["app_status"] if context else None,
                    "blocked": bool(context["blocked"]) if context else False,
                    "availability_label": availability_label(
                        status, bool(bay["reservation_supported"])
                    ),
                }
            )
        return output

    def reservations(
        self, status: str | None = None, bay_id: str | None = None, search: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if status:
            clauses.append("app_reservations.status = ?")
            values.append(status)
        if bay_id:
            clauses.append("app_reservations.bay_id = ?")
            values.append(bay_id)
        if search:
            clauses.append("(users.email LIKE ? OR stations.name LIKE ? OR app_reservations.id LIKE ?)")
            values.extend([f"%{search}%"] * 3)
        query = RESERVATION_SELECT
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY app_reservations.created_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        views = [reservation_view(row) for row in rows]
        for view in views:
            view["license_plate"] = mask_plate(view["license_plate"])
        return views

    def events(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM processed_events ORDER BY timestamp DESC LIMIT 200"
            ).fetchall()
        return [
            {
                "external_event_id": row["external_event_id"],
                "event_type": row["event_type"],
                "bay_id": row["bay_id"],
                "reservation_id": row["reservation_id"],
                "masked_plate": row["masked_plate"],
                "confidence": row["confidence"],
                "processing_result": row["processing_result"],
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]

    def alerts(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC"
            ).fetchall()
        return [alert_view(row) for row in rows]

    def acknowledge_alert(self, alert_id: str, note: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            changed = connection.execute(
                """
                UPDATE alerts SET status = 'ACKNOWLEDGED', note = ?
                WHERE id = ? AND status = 'OPEN'
                """,
                (note, alert_id),
            ).rowcount
            if not changed:
                row = connection.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
                if row is None:
                    raise not_found("Alert was not found")
            row = connection.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
            assert row is not None
            return alert_view(row)

    def analytics(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM app_reservations").fetchone()[0]
            completed = connection.execute(
                "SELECT COUNT(*) FROM app_reservations WHERE status = 'COMPLETED'"
            ).fetchone()[0]
            checked_in = connection.execute(
                "SELECT COUNT(*) FROM app_reservations WHERE status IN ('CHECKED_IN','CHARGING','CHARGE_COMPLETED','COMPLETED')"
            ).fetchone()[0]
            expired = connection.execute(
                "SELECT COUNT(*) FROM app_reservations WHERE status = 'EXPIRED'"
            ).fetchone()[0]
            cancelled = connection.execute(
                "SELECT COUNT(*) FROM app_reservations WHERE status = 'CANCELLED'"
            ).fetchone()[0]
            blocked = connection.execute(
                "SELECT COUNT(*) FROM alerts WHERE alert_type = 'BLOCKED_BAY'"
            ).fetchone()[0]
            hours = connection.execute(
                """
                SELECT COALESCE(SUM((julianday(end_time)-julianday(start_time))*24), 0)
                FROM app_reservations
                """
            ).fetchone()[0]
        return {
            "label": "Simulation / Estimated",
            "total_reservations": total,
            "check_in_rate": round((checked_in / total * 100) if total else 0, 1),
            "no_show_rate": round((expired / total * 100) if total else 0, 1),
            "cancellation_rate": round((cancelled / total * 100) if total else 0, 1),
            "blocked_bay_events": blocked,
            "reserved_hours": round(float(hours), 1),
            "assumptions": {
                "reservation_fee_eur": self.settings.reservation_fee_eur,
                "no_show_fee_eur": self.settings.no_show_fee_eur,
            },
            "estimated_revenue_eur": round(
                completed * self.settings.reservation_fee_eur
                + expired * self.settings.no_show_fee_eur,
                2,
            ),
        }
