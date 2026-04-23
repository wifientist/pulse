from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


class SPAStaticFiles(StaticFiles):
    """StaticFiles + SPA fallback: serve index.html for any unmatched path so client-
    side routes like /agents load correctly on hard-refresh / deep-link. API routes are
    still preferred because they're mounted on the FastAPI app before this."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404:
                return await super().get_response("index.html", scope)
            raise

from pulse_server.config import Settings, load_settings
from pulse_server.db.engine import build_engine, build_sessionmaker
from pulse_server.logging import configure_logging, get_logger
from pulse_server.routers import (
    access_points,
    agents,
    alerts,
    boosts,
    debug,
    enrollment,
    events,
    groups,
    health,
    passive_targets,
    peers,
    tags,
    telemetry,
    tests,
    trends,
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
    app.include_router(agents.agent_package_router)
    app.include_router(groups.router)
    app.include_router(tags.router)
    app.include_router(peers.router)
    app.include_router(tests.router)
    app.include_router(webhooks.router)
    app.include_router(alerts.router)
    app.include_router(access_points.router)
    app.include_router(passive_targets.router)
    app.include_router(boosts.router)
    app.include_router(trends.router)
    app.include_router(events.router)
    app.include_router(debug.router)

    # Serve the built web UI at "/" when present. Must be mounted AFTER all routers so
    # /v1/* and /healthz take precedence. In dev the Vite dev server serves the UI
    # directly and this mount is a no-op (web_dist_dir unset or directory absent).
    if settings.web_dist_dir:
        dist = Path(settings.web_dist_dir)
        if dist.is_dir():
            app.mount("/", SPAStaticFiles(directory=dist, html=True), name="spa")
            log.info("spa.mounted", dist=str(dist))

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
