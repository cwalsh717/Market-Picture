"""APScheduler configuration and lifecycle management."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.jobs.daily_update import (
    fetch_fred_quotes,
    fetch_premarket_quotes,
    fetch_twelve_data_quotes,
    generate_close_summary,
    generate_premarket_summary,
)
from backend.intelligence.narrative_data import fetch_technical_signals
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

    # -- FRED: weekdays at 3:30 PM ET ---------------------------------------
    scheduler.add_job(
        fetch_fred_quotes,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=30,
        id="fred_quotes",
        name="Fetch FRED rates and credit spreads",
        kwargs={"provider": fred},
        replace_existing=True,
        max_instances=1,
    )

    # -- Pre-market quote refresh: weekdays 7:45 AM ET ---------------------
    scheduler.add_job(
        fetch_premarket_quotes,
        trigger="cron",
        day_of_week="mon-fri",
        hour=7,
        minute=45,
        id="premarket_quotes",
        name="Pre-market quote refresh (with extended hours)",
        kwargs={"provider": twelve_data},
        replace_existing=True,
        max_instances=1,
    )

    # -- LLM pre-market summary: weekdays 9:45 AM ET --------------------
    scheduler.add_job(
        generate_premarket_summary,
        trigger="cron",
        day_of_week="mon-fri",
        hour=9,
        minute=45,
        id="premarket_summary",
        name="Generate pre-market LLM summary",
        replace_existing=True,
        max_instances=1,
    )

    # -- Technical indicators fetch: weekdays 4:35 PM ET -----------------
    scheduler.add_job(
        fetch_technical_signals,
        trigger="cron",
        day_of_week="mon-fri",
        hour=16,
        minute=35,
        id="technical_signals",
        name="Fetch technical indicators for key symbols",
        replace_existing=True,
        max_instances=1,
    )

    # -- LLM after-close summary: weekdays 4:50 PM ET -------------------
    scheduler.add_job(
        generate_close_summary,
        trigger="cron",
        day_of_week="mon-fri",
        hour=16,
        minute=50,
        id="close_summary",
        name="Generate after-close LLM summary",
        replace_existing=True,
        max_instances=1,
    )

    # -- Daily history cache update: weekdays 4:45 PM ET ------------------
    scheduler.add_job(
        daily_append_all,
        trigger="cron",
        day_of_week="mon-fri",
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
