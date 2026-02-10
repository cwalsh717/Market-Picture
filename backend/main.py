"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from backend.db import init_db
from backend.jobs.scheduler import start_scheduler, stop_scheduler
from backend.providers.fred import FredProvider
from backend.providers.twelve_data import TwelveDataProvider

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup, clean up on shutdown."""
    await init_db()
    app.state.twelve_data = TwelveDataProvider()
    app.state.fred = FredProvider()
    app.state.scheduler = start_scheduler(
        twelve_data=app.state.twelve_data,
        fred=app.state.fred,
    )
    logger.info("Market Picture started")
    yield
    stop_scheduler()
    await app.state.fred.close()
    await app.state.twelve_data.close()
    logger.info("Market Picture stopped")


app = FastAPI(title="Market Picture", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict:
    """Return service health status."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
