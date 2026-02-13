"""APScheduler configuration and lifecycle management."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.jobs.daily_update import (
    fetch_fred_quotes,
    fetch_twelve_data_quotes,
    generate_close_summary,
    generate_premarket_summary,
)
from backend.services.history_cache import daily_append_all
from backend.providers.fred import FredProvider
from backend.providers.twelve_data import TwelveDataProvider

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def create_scheduler(
    twelve_data: TwelveDataProvider,
    fred: FredProvider,
) -> AsyncIOScheduler:
    """Create and configure the scheduler with all jobs.

    Providers are passed as job kwargs so job functions remain testable
    without global state.
    """
    scheduler = AsyncIOScheduler(timezone="US/Eastern")

    # -- Twelve Data: every 10 minutes, checks market hours at runtime ------
    scheduler.add_job(
        fetch_twelve_data_quotes,
        trigger="interval",
        minutes=10,
        id="twelve_data_quotes",
        name="Fetch Twelve Data quotes (open markets only)",
        kwargs={"provider": twelve_data},
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # -- FRED: once daily at 3:30 PM ET ------------------------------------
    scheduler.add_job(
        fetch_fred_quotes,
        trigger="cron",
        hour=15,
        minute=30,
        id="fred_quotes",
        name="Fetch FRED rates and credit spreads",
        kwargs={"provider": fred},
        replace_existing=True,
        max_instances=1,
    )

    # -- LLM pre-market summary: 8:00 AM ET (placeholder) ------------------
    scheduler.add_job(
        generate_premarket_summary,
        trigger="cron",
        hour=8,
        minute=0,
        id="premarket_summary",
        name="Generate pre-market LLM summary",
        replace_existing=True,
        max_instances=1,
    )

    # -- LLM after-close summary: 4:30 PM ET (placeholder) -----------------
    scheduler.add_job(
        generate_close_summary,
        trigger="cron",
        hour=16,
        minute=30,
        id="close_summary",
        name="Generate after-close LLM summary",
        replace_existing=True,
        max_instances=1,
    )

    # -- Daily history cache update: 4:45 PM ET ----------------------------
    scheduler.add_job(
        daily_append_all,
        trigger="cron",
        hour=16,
        minute=45,
        id="daily_history_append",
        name="Append latest bar for all cached symbols",
        kwargs={"provider": twelve_data},
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    return scheduler


def start_scheduler(
    twelve_data: TwelveDataProvider,
    fred: FredProvider,
) -> AsyncIOScheduler:
    """Create, start, and return the scheduler."""
    global _scheduler
    _scheduler = create_scheduler(twelve_data, fred)
    _scheduler.start()
    logger.info("Scheduler started with %d jobs", len(_scheduler.get_jobs()))
    return _scheduler


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
        _scheduler = None
