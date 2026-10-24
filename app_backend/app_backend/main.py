from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app_backend.adapters.base import EcuriousAdapter, ProviderError
from app_backend.adapters.http_mock import HttpMockEcuriousAdapter
from app_backend.api import admin, auth, reservations, stations, vehicles
from app_backend.app_backend.utils.config import Settings
from app_backend.app_backend.utils.database import Database
from app_backend.app_backend.utils.errors import AppError
from app_backend.services.event_sync import EventSyncService


logger = logging.getLogger("ecurious_app")


def create_app(
    settings: Settings | None = None,
    adapter: EcuriousAdapter | None = None,
    *,
    initialize_database: bool = True,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    logging.getLogger("ecurious_app").setLevel(
        getattr(logging, resolved.log_level, logging.INFO)
    )
    database = Database(resolved)
    provider = adapter or HttpMockEcuriousAdapter(
        resolved.ecurious_base_url, resolved.ecurious_token
    )
    event_sync = EventSyncService(database, provider, resolved)

    async def polling_loop() -> None:
        while True:
            try:
                await event_sync.sync_once()
            except ProviderError as exc:
                logger.warning("Event sync provider error: %s", exc.code)
            except Exception:
                logger.exception("Unexpected event sync failure")
            await asyncio.sleep(resolved.event_poll_seconds)

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        database.initialize()
        task = (
            asyncio.create_task(polling_loop(), name="ecurious-event-sync")
            if resolved.event_poll_enabled
            else None
        )
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    application = FastAPI(
        title="Ecurious EV Reservation App Backend",
        version="1.0.0",
        description=(
            "User-facing application API. It integrates with the fictional Ecurious Mock "
            "through a replaceable adapter and does not represent an official API."
        ),
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.database = database
    application.state.adapter = provider
    application.state.event_sync = event_sync
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @application.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "message": exc.message},
            headers=headers,
        )

    @application.exception_handler(ProviderError)
    async def provider_error_handler(_request: Request, exc: ProviderError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(item) for item in first.get("loc", []))
        message = first.get("msg", "Invalid request")
        return JSONResponse(
            status_code=422,
            content={
                "error": "INVALID_REQUEST",
                "message": f"{location}: {message}" if location else message,
            },
        )

    @application.get("/api/health", tags=["system"])
    def health():
        return {"status": "ok", "provider": "fictional-ecurious-mock"}

    application.include_router(auth.router)
    application.include_router(vehicles.router)
    application.include_router(stations.router)
    application.include_router(reservations.router)
    application.include_router(admin.router)
    if initialize_database:
        database.initialize()
    return application


app = create_app(initialize_database=False)

