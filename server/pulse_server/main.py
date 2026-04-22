from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from pulse_server.config import Settings, load_settings
from pulse_server.db.engine import build_engine, build_sessionmaker
from pulse_server.logging import configure_logging, get_logger
from pulse_server.routers import (
    agents,
    alerts,
    debug,
    enrollment,
    groups,
    health,
    peers,
    tags,
    telemetry,
    tests,
    webhooks,
)
from pulse_server.scheduler.jobs import build_scheduler


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    log = get_logger(__name__)

    engine = build_engine(settings.db_url)
    sessionmaker = build_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        scheduler = build_scheduler(settings, sessionmaker)
        app.state.scheduler = scheduler
        scheduler.start()
        log.info("pulse.started", bind=f"{settings.bind_host}:{settings.bind_port}")
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
            await engine.dispose()
            log.info("pulse.stopped")

    app = FastAPI(
        title="Pulse — Peer Uptime & Link Status Engine",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(enrollment.router)
    app.include_router(telemetry.router)
    app.include_router(agents.router)
    app.include_router(groups.router)
    app.include_router(tags.router)
    app.include_router(peers.router)
    app.include_router(tests.router)
    app.include_router(webhooks.router)
    app.include_router(alerts.router)
    app.include_router(debug.router)

    return app


def run() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "pulse_server.main:create_app",
        factory=True,
        host=settings.bind_host,
        port=settings.bind_port,
        log_config=None,
    )


if __name__ == "__main__":
    run()
