from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from app_backend.app_backend.utils.database import Database, now_iso
from app_backend.app_backend.utils.errors import conflict, not_found
from app_backend.app_backend.utils.schemas import VehicleCreate, VehicleUpdate


def vehicle_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "license_plate": row["license_plate"],
        "nickname": row["nickname"],
        "is_default": bool(row["is_default"]),
        "created_at": row["created_at"],
    }


class VehicleService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM vehicles WHERE user_id = ? ORDER BY is_default DESC, created_at",
                (user_id,),
            ).fetchall()
        return [vehicle_view(row) for row in rows]

    def create(self, user_id: str, payload: VehicleCreate) -> dict[str, Any]:
        vehicle_id = f"VEH-{uuid.uuid4().hex[:12].upper()}"
        try:
            with self.database.transaction(immediate=True) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM vehicles WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
                make_default = payload.is_default or count == 0
                if make_default:
                    connection.execute(
                        "UPDATE vehicles SET is_default = 0, updated_at = ? WHERE user_id = ?",
                        (now_iso(), user_id),
                    )
                timestamp = now_iso()
                connection.execute(
                    """
                    INSERT INTO vehicles(
                        id, user_id, license_plate, nickname, is_default, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vehicle_id,
                        user_id,
                        payload.license_plate,
                        payload.nickname.strip(),
                        int(make_default),
                        timestamp,
                        timestamp,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)
                ).fetchone()
                assert row is not None
                return vehicle_view(row)
        except sqlite3.IntegrityError as exc:
            raise conflict("VEHICLE_ALREADY_EXISTS", "This license plate is already registered") from exc

    def update(
        self, user_id: str, vehicle_id: str, payload: VehicleUpdate
    ) -> dict[str, Any]:
        values = payload.model_dump(exclude_unset=True)
        if not values:
            raise conflict("NO_CHANGES", "No vehicle changes were provided")
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM vehicles WHERE id = ? AND user_id = ?", (vehicle_id, user_id)
            ).fetchone()
            if row is None:
                raise not_found("Vehicle was not found")
            if values.get("is_default"):
                connection.execute(
                    "UPDATE vehicles SET is_default = 0, updated_at = ? WHERE user_id = ?",
                    (now_iso(), user_id),
                )
            updated = {
                "license_plate": values.get("license_plate", row["license_plate"]),
                "nickname": values.get("nickname", row["nickname"]).strip(),
                "is_default": int(values.get("is_default", bool(row["is_default"]))),
            }
            try:
                connection.execute(
                    """
                    UPDATE vehicles SET license_plate = ?, nickname = ?, is_default = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        updated["license_plate"],
                        updated["nickname"],
                        updated["is_default"],
                        now_iso(),
                        vehicle_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise conflict(
                    "VEHICLE_ALREADY_EXISTS", "This license plate is already registered"
                ) from exc
            result = connection.execute(
                "SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)
            ).fetchone()
            assert result is not None
            return vehicle_view(result)

    def delete(self, user_id: str, vehicle_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM vehicles WHERE id = ? AND user_id = ?", (vehicle_id, user_id)
            ).fetchone()
            if row is None:
                raise not_found("Vehicle was not found")
            in_use = connection.execute(
                """
                SELECT 1 FROM app_reservations
                WHERE vehicle_id = ? AND status NOT IN ('CANCELLED', 'COMPLETED', 'EXPIRED')
                LIMIT 1
                """,
                (vehicle_id,),
            ).fetchone()
            if in_use:
                raise conflict("VEHICLE_IN_USE", "Vehicle has an active reservation")
            connection.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
            if row["is_default"]:
                next_row = connection.execute(
                    "SELECT id FROM vehicles WHERE user_id = ? ORDER BY created_at LIMIT 1",
                    (user_id,),
                ).fetchone()
                if next_row:
                    connection.execute(
                        "UPDATE vehicles SET is_default = 1, updated_at = ? WHERE id = ?",
                        (now_iso(), next_row["id"]),
                    )

