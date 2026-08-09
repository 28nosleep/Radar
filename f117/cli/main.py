from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from dataclasses import asdict
from time import monotonic
from typing import NoReturn

from f117 import __version__
from f117.config import Settings
from f117.services.runtime import (
    ConfigurationError,
    build_digest_service,
    validate_settings,
)
from f117.storage.database import Database
from f117.storage.repository import Repository

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Radar — personal RSS intelligence digest",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-once", help="run one complete RSS-to-Telegram cycle")
    subparsers.add_parser("scheduler", help="run immediately and then on an interval")
    subparsers.add_parser("status", help="print compact database counters")
    subparsers.add_parser("validate-config", help="validate .env and the RSS catalog")
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
                    "enabled_rss_sources": sum(source.enabled for source in sources),
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


async def _run_scheduler(settings: Settings) -> None:
    database = Database(settings.database_url)
    repository = Repository(database)
    service = build_digest_service(settings, repository)
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
    try:
        while not stop_event.is_set():
            started_at = monotonic()
            try:
                summary = await service.run_once()
                logger.info("Digest run completed: %s", summary)
            except Exception:
                logger.exception("Scheduled digest run failed; the scheduler will continue")

            elapsed = monotonic() - started_at
            interval = settings.scheduler_interval_minutes * 60.0
            timeout = max(1.0, interval - elapsed)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=timeout)
            except TimeoutError:
                continue
    finally:
        await database.dispose()
        logger.info("Scheduler stopped")


def _configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _configuration_exit(exc: ConfigurationError) -> NoReturn:
    raise SystemExit(f"Invalid Radar configuration:\n{exc}")


if __name__ == "__main__":
    main()
