"""FastAPI app factory (Phase 9)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from netmon.config import Settings
from netmon.db import create_all_tables, dispose_engine, init_engine
from netmon.logging_setup import configure_logging
from netmon.scheduler import Scheduler
from netmon.web.auth import LoginRequiredMiddleware
from netmon.web.routes_api import router as api_router
from netmon.web.routes_auth import router as auth_router
from netmon.web.routes_pages import router as pages_router

logger = logging.getLogger("netmon.web")


def create_app(settings: Settings) -> FastAPI:
    configure_logging(settings.logging)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_engine(settings)
        await create_all_tables()  # no-op for tables Alembic already created; bootstraps a brand new DB

        scheduler = Scheduler(settings)
        await scheduler.start()
        app.state.scheduler = scheduler

        logger.info("netmon started")
        try:
            yield
        finally:
            await scheduler.stop()
            await dispose_engine()
            logger.info("netmon stopped")

    app = FastAPI(title="Home Network Monitor", lifespan=lifespan)
    app.state.settings = settings

    # add_middleware() prepends, so the LAST call here becomes the OUTERMOST
    # middleware. SessionMiddleware must run first on the way in (so
    # request.session exists) and last on the way out -- it has to be
    # outermost, so it's added second.
    app.add_middleware(LoginRequiredMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.dashboard.session_secret,
        max_age=settings.dashboard.session_max_age_hours * 3600,
        same_site="lax",
    )

    app.mount("/static", StaticFiles(directory="netmon/web/static"), name="static")

    app.include_router(auth_router)
    app.include_router(pages_router)
    app.include_router(api_router)

    return app
