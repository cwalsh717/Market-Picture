"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from backend.db import init_db
from backend.providers.twelve_data import TwelveDataProvider


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup, clean up on shutdown."""
    await init_db()
    app.state.twelve_data = TwelveDataProvider()
    yield
    await app.state.twelve_data.close()


app = FastAPI(title="Market Picture", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict:
    """Return service health status."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
