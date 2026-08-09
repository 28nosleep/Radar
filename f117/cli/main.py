from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from dataclasses import asdict
from time import monotonic
from typing import NoReturn
from uuid import UUID

from f117 import __version__
from f117.adapters.telegram import TelegramFeedbackPoller
from f117.config import Settings
from f117.services.reports import discovery_report, quality_report
from f117.services.runtime import (
    ConfigurationError,
    build_digest_service,
    build_feedback_poller,
    validate_settings,
)
from f117.storage.database import Database
from f117.storage.repository import Repository

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Radar — personal intelligence digest",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-once", help="run one complete source-to-Telegram cycle")
    subparsers.add_parser("scheduler", help="run on the configured interval")
    subparsers.add_parser("status", help="print compact database counters")
    subparsers.add_parser("validate-config", help="validate .env and the source catalog")
    quality = subparsers.add_parser(
        "quality-report", help="show source quality from owner feedback"
    )
    quality.add_argument("--days", type=_positive_days, default=7)
    discovery = subparsers.add_parser("discovery-report", help="show discovery score calibration")
    discovery.add_argument("--days", type=_positive_days, default=7)
    subparsers.add_parser("poll-feedback", help="process pending Telegram inline-button feedback")
    recover_delivery = subparsers.add_parser(
        "retry-delivery", help="explicitly release one ambiguous Telegram delivery for retry"
    )
    recover_delivery.add_argument("material_id", type=UUID)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    _configure_logging(settings.log_level)
    try:
        validate_settings(settings)
    except ConfigurationError as exc:
        _configuration_exit(exc)

    if args.command == "validate-config":
        sources = settings.load_feed_sources()
        print(
            json.dumps(
                {
                    "status": "ok",
                    "enabled_sources": sum(source.enabled for source in sources),
                    "dry_run": settings.dry_run,
                    "openai_enabled": settings.openai_enabled,
                    "telegram_enabled": settings.telegram_enabled,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "run-once":
        asyncio.run(_run_once(settings))
    elif args.command == "scheduler":
        asyncio.run(_run_scheduler(settings))
    elif args.command == "status":
        asyncio.run(_show_status(settings))
    elif args.command == "quality-report":
        asyncio.run(_show_quality_report(settings, days=args.days))
    elif args.command == "discovery-report":
        asyncio.run(_show_discovery_report(settings, days=args.days))
    elif args.command == "poll-feedback":
        asyncio.run(_poll_feedback_once(settings))
    elif args.command == "retry-delivery":
        asyncio.run(_recover_delivery(settings, material_id=args.material_id))


async def _run_once(settings: Settings) -> None:
    database = Database(settings.database_url)
    repository = Repository(database)
    service = build_digest_service(settings, repository)
    try:
        summary = await service.run_once()
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2, default=str))
    finally:
        await database.dispose()


async def _show_status(settings: Settings) -> None:
    database = Database(settings.database_url)
    try:
        counts = await Repository(database).counts()
        print(json.dumps(counts, ensure_ascii=False, indent=2))
    finally:
        await database.dispose()


async def _show_quality_report(settings: Settings, *, days: int) -> None:
    database = Database(settings.database_url)
    try:
        report = await quality_report(Repository(database), days=days)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await database.dispose()


async def _show_discovery_report(settings: Settings, *, days: int) -> None:
    database = Database(settings.database_url)
    try:
        report = await discovery_report(
            Repository(database),
            days=days,
            rising_threshold=settings.discovery_rising_threshold,
            hidden_gem_max_popularity=settings.discovery_hidden_gem_max_popularity,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await database.dispose()


async def _poll_feedback_once(settings: Settings) -> None:
    database = Database(settings.database_url)
    try:
        poller = build_feedback_poller(settings, Repository(database))
        if poller is None:
            print(json.dumps({"processed": 0, "reason": "Telegram feedback is disabled"}))
            return
        print(json.dumps({"processed": await poller.poll_once()}))
    finally:
        await database.dispose()


async def _recover_delivery(settings: Settings, *, material_id: UUID) -> None:
    database = Database(settings.database_url)
    try:
        recovered = await Repository(database).recover_ambiguous_delivery(material_id)
        print(json.dumps({"recovered": recovered, "material_id": str(material_id)}))
    finally:
        await database.dispose()


async def _run_scheduler(settings: Settings) -> None:
    database = Database(settings.database_url)
    repository = Repository(database)
    service = build_digest_service(settings, repository)
    feedback_poller = build_feedback_poller(settings, repository)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(stop_signal, stop_event.set)
        except NotImplementedError:
            pass

    logger.info(
        "Scheduler started; interval=%s minutes, dry_run=%s",
        settings.scheduler_interval_minutes,
        settings.dry_run,
    )
    feedback_task = (
        asyncio.create_task(
            _feedback_loop(feedback_poller, settings.telegram_feedback_poll_seconds)
        )
        if feedback_poller is not None
        else None
    )
    try:
        # Starting a container must not turn any undelivered candidates into an
        # unscheduled digest.  In particular, this leaves the persisted delivery
        # retry and ambiguous-delivery lifecycle untouched until the next regular
        # scheduler tick.  `radar run-once` remains the explicit immediate path.
        interval = settings.scheduler_interval_minutes * 60.0
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
        while not stop_event.is_set():
            started_at = monotonic()
            try:
                summary = await service.run_once()
                logger.info("Digest run completed: %s", summary)
            except Exception:
                logger.exception("Scheduled digest run failed; the scheduler will continue")
            timeout = max(1.0, interval - (monotonic() - started_at))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=timeout)
            except TimeoutError:
                continue
    finally:
        if feedback_task is not None:
            feedback_task.cancel()
            await asyncio.gather(feedback_task, return_exceptions=True)
        await database.dispose()
        logger.info("Scheduler stopped")


async def _feedback_loop(poller: TelegramFeedbackPoller, interval_seconds: int) -> None:
    while True:
        try:
            processed = await poller.poll_once()
            if processed:
                logger.info("Processed %s Telegram feedback updates", processed)
        except Exception:
            logger.exception("Telegram feedback polling failed; will retry")
        await asyncio.sleep(interval_seconds)


def _configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _configuration_exit(exc: ConfigurationError) -> NoReturn:
    raise SystemExit(f"Invalid Radar configuration:\n{exc}")


def _positive_days(value: str) -> int:
    days = int(value)
    if days < 1 or days > 365:
        raise argparse.ArgumentTypeError("days must be between 1 and 365")
    return days


if __name__ == "__main__":
    main()
