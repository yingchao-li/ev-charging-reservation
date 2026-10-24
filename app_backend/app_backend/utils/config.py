from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite:///./ecurious_app.db"
    ecurious_base_url: str = "http://127.0.0.1:8000"
    ecurious_token: str = "local-dev-token"
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")
    session_hours: int = 24
    event_poll_enabled: bool = True
    event_poll_seconds: float = 3.0
    sync_lease_seconds: int = 15
    station_fixture_path: str = str(PROJECT_ROOT / "goingelectric_response.json")
    seed_demo_users: bool = False
    demo_driver_email: str = "driver@demo.local"
    demo_driver_password: str = ""
    demo_admin_email: str = "admin@demo.local"
    demo_admin_password: str = ""
    reservation_fee_eur: float = 2.5
    no_show_fee_eur: float = 5.0
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        origins = tuple(
            item.strip()
            for item in os.getenv(
                "APP_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
            ).split(",")
            if item.strip()
        )
        return cls(
            database_url=os.getenv("APP_DATABASE_URL", "sqlite:///./ecurious_app.db"),
            ecurious_base_url=os.getenv(
                "ECURIOUS_BASE_URL", "http://127.0.0.1:8000"
            ).rstrip("/"),
            ecurious_token=os.getenv("ECURIOUS_MOCK_TOKEN", "local-dev-token"),
            cors_origins=origins,
            session_hours=int(os.getenv("APP_SESSION_HOURS", "24")),
            event_poll_enabled=_bool("APP_EVENT_POLL_ENABLED", "true"),
            event_poll_seconds=float(os.getenv("APP_EVENT_POLL_SECONDS", "3")),
            sync_lease_seconds=int(os.getenv("APP_SYNC_LEASE_SECONDS", "15")),
            station_fixture_path=os.getenv(
                "STATION_FIXTURE_PATH", str(PROJECT_ROOT / "goingelectric_response.json")
            ),
            seed_demo_users=_bool("APP_SEED_DEMO_USERS", "false"),
            demo_driver_email=os.getenv("DEMO_DRIVER_EMAIL", "driver@demo.local"),
            demo_driver_password=os.getenv("DEMO_DRIVER_PASSWORD", ""),
            demo_admin_email=os.getenv("DEMO_ADMIN_EMAIL", "admin@demo.local"),
            demo_admin_password=os.getenv("DEMO_ADMIN_PASSWORD", ""),
            reservation_fee_eur=float(os.getenv("RESERVATION_FEE_EUR", "2.5")),
            no_show_fee_eur=float(os.getenv("NO_SHOW_FEE_EUR", "5.0")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def database_path(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// APP_DATABASE_URL values are supported")
        return str(Path(self.database_url[len(prefix) :]).expanduser())

