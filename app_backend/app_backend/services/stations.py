from __future__ import annotations

import json
import sqlite3
from typing import Any

from app_backend.adapters.base import EcuriousAdapter, ProviderError
from app_backend.app_backend.utils.database import Database
from app_backend.app_backend.utils.errors import not_found


def availability_label(status: dict[str, Any] | None, supported: bool) -> str:
    if not supported:
        return "Availability Unknown"
    if status is None:
        return "Provider Unavailable"
    if status["occupancy"] == "unknown" or status["protection"] == "unknown":
        return "Availability Unknown"
    if status["occupancy"] == "occupied":
        return "Occupied"
    if status.get("active_reservation_id") or status["protection"] == "protected":
        return "Reserved"
    return "Free (Mock live status)"


def _chargepoints(row: sqlite3.Row) -> list[dict[str, Any]]:
    return [
        {
            "type": str(item.get("type", "Unknown")).replace("Typ", "Type"),
            "power": float(item.get("power") or 0),
            "count": int(item.get("count") or 0),
        }
        for item in json.loads(row["chargepoints_json"])
    ]


class StationService:
    def __init__(self, database: Database, adapter: EcuriousAdapter) -> None:
        self.database = database
        self.adapter = adapter

    async def _bay_support(self) -> dict[str, bool]:
        try:
            return {
                bay["bay_id"]: bool(bay["reservation_supported"])
                for bay in await self.adapter.list_bays()
            }
        except ProviderError:
            return {}

    async def list(
        self,
        *,
        search: str | None = None,
        connector: str | None = None,
        min_power: float = 0,
        network: str | None = None,
        ne_lat: float | None = None,
        ne_lng: float | None = None,
        sw_lat: float | None = None,
        sw_lng: float | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT stations.*, GROUP_CONCAT(bay_mappings.bay_id) AS bay_ids
                FROM stations
                LEFT JOIN bay_mappings ON bay_mappings.station_id = stations.id
                GROUP BY stations.id
                ORDER BY stations.name
                """
            ).fetchall()
        support = await self._bay_support()
        result: list[dict[str, Any]] = []
        for row in rows:
            chargepoints = _chargepoints(row)
            haystack = " ".join(
                [row["name"], row["city"], row["street"], row["network"] or ""]
            ).lower()
            if search and search.lower() not in haystack:
                continue
            if connector and connector != "All" and not any(
                connector.lower() in point["type"].lower() for point in chargepoints
            ):
                continue
            if max((point["power"] for point in chargepoints), default=0) < min_power:
                continue
            if network and network != "All" and (row["network"] or "") != network:
                continue
            if None not in {ne_lat, ne_lng, sw_lat, sw_lng} and not (
                sw_lat <= row["lat"] <= ne_lat and sw_lng <= row["lng"] <= ne_lng
            ):
                continue
            result.append(self._view(row, support, chargepoints))
        return result

    async def get(self, station_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT stations.*, GROUP_CONCAT(bay_mappings.bay_id) AS bay_ids
                FROM stations
                LEFT JOIN bay_mappings ON bay_mappings.station_id = stations.id
                WHERE stations.id = ? GROUP BY stations.id
                """,
                (station_id,),
            ).fetchone()
        if row is None:
            raise not_found("Station was not found")
        view = self._view(row, await self._bay_support(), _chargepoints(row))
        view["country"] = row["country"]
        return view

    async def availability(self, bay_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            mapping = connection.execute(
                "SELECT station_id FROM bay_mappings WHERE bay_id = ?", (bay_id,)
            ).fetchone()
        if mapping is None:
            raise not_found("Mapped bay was not found")
        support = await self._bay_support()
        supported = support.get(bay_id, False)
        status = await self.adapter.get_bay_status(bay_id)
        return {
            **status,
            "station_id": mapping["station_id"],
            "reservation_supported": supported,
            "availability_label": availability_label(status, supported),
        }

    @staticmethod
    def _view(
        row: sqlite3.Row,
        support: dict[str, bool],
        chargepoints: list[dict[str, Any]],
    ) -> dict[str, Any]:
        bay_ids = row["bay_ids"].split(",") if row["bay_ids"] else []
        reservable = any(support.get(bay_id, False) for bay_id in bay_ids)
        return {
            "id": row["id"],
            "external_station_id": row["external_station_id"],
            "source": row["source"],
            "name": row["name"],
            "city": row["city"],
            "postcode": row["postcode"],
            "street": row["street"],
            "lat": row["lat"],
            "lng": row["lng"],
            "network": row["network"],
            "verified": bool(row["verified"]),
            "fault": bool(row["fault"]),
            "chargepoints": chargepoints,
            "source_url": row["source_url"],
            "ecurious_bay_ids": bay_ids,
            "reservation_supported": reservable,
            "availability_label": (
                "Reservable · Check live status" if reservable else "Availability Unknown"
            ),
        }

