from __future__ import annotations

from f117.adapters.arxiv import ArxivCollector
from f117.adapters.collectors import SourceCollector
from f117.adapters.github import GitHubCollector
from f117.adapters.hacker_news import HackerNewsCollector
from f117.adapters.openai_editorial import (
    DeterministicEditorialEnricher,
    EditorialEnricher,
    OpenAIEditorialEnricher,
    ResilientEditorialEnricher,
)
from f117.adapters.reddit import RedditCollector
from f117.adapters.rss import RSSCollector
from f117.adapters.telegram import (
    DigestNotifier,
    DryRunNotifier,
    TelegramFeedbackPoller,
    TelegramNotifier,
)
from f117.adapters.youtube import YouTubeCollector
from f117.config import Settings
from f117.services.digest import DigestService
from f117.storage.repository import Repository


class ConfigurationError(ValueError):
    pass


def validate_settings(settings: Settings) -> None:
    errors: list[str] = []
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        errors.append("F117_DATABASE_URL must use postgresql+asyncpg://")

    try:
        sources = settings.load_feed_sources()
    except (OSError, ValueError) as exc:
        errors.append(f"Source catalog cannot be loaded: {exc}")
        sources = []
    enabled_sources = [source for source in sources if source.enabled]
    if not enabled_sources:
        errors.append("Source catalog must contain at least one enabled source")
    source_keys = [source.key for source in sources]
    if len(source_keys) != len(set(source_keys)):
        errors.append("RSS catalog source keys must be unique")

    openai_key = _secret_value(settings.openai_api_key)
    if settings.openai_enabled and not openai_key:
        errors.append("F117_OPENAI_API_KEY is required when OpenAI is enabled")

    telegram_token = _secret_value(settings.telegram_bot_token)
    if settings.telegram_enabled and not telegram_token:
        errors.append("F117_TELEGRAM_BOT_TOKEN is required when Telegram is enabled")
    if settings.telegram_enabled and not _is_private_chat_id(settings.telegram_chat_id):
        errors.append("F117_TELEGRAM_CHAT_ID must be a positive private-chat ID")

    if not settings.dry_run:
        if not settings.openai_enabled:
            errors.append("OpenAI must be enabled for a real delivery")
        if not settings.telegram_enabled:
            errors.append("Telegram must be enabled when F117_DRY_RUN=false")

    if errors:
        raise ConfigurationError("\n".join(f"- {error}" for error in errors))


def build_digest_service(settings: Settings, repository: Repository) -> DigestService:
    deterministic = DeterministicEditorialEnricher()
    enricher: EditorialEnricher
    if settings.openai_enabled and not settings.dry_run:
        api_key = _secret_value(settings.openai_api_key)
        if not api_key:
            raise ConfigurationError("F117_OPENAI_API_KEY is missing")
        primary = OpenAIEditorialEnricher(
            api_key=api_key,
            model=settings.openai_model,
            reasoning_effort=settings.openai_reasoning_effort,
            max_output_tokens=settings.openai_max_output_tokens,
            max_concurrency=settings.openai_max_concurrency,
        )
        enricher = ResilientEditorialEnricher(primary, deterministic)
    else:
        enricher = deterministic

    notifier: DigestNotifier
    if settings.dry_run:
        notifier = DryRunNotifier()
    else:
        token = _secret_value(settings.telegram_bot_token)
        if not token or settings.telegram_chat_id is None:
            raise ConfigurationError("Telegram credentials are missing")
        notifier = TelegramNotifier(
            bot_token=token,
            chat_id=settings.telegram_chat_id,
            api_base=settings.telegram_api_base,
            timeout_seconds=settings.http_timeout_seconds,
            debug=settings.telegram_format == "debug",
            pace_seconds=settings.telegram_pace_seconds,
        )

    rss = RSSCollector(
        timeout_seconds=settings.http_timeout_seconds,
        max_response_bytes=settings.http_max_response_bytes,
        user_agent=settings.http_user_agent,
    )
    collector = SourceCollector(
        rss=rss,
        hacker_news=HackerNewsCollector(
            timeout_seconds=settings.http_timeout_seconds,
            user_agent=settings.http_user_agent,
        ),
        arxiv=ArxivCollector(
            timeout_seconds=settings.http_timeout_seconds,
            max_response_bytes=settings.http_max_response_bytes,
            user_agent=settings.http_user_agent,
        ),
        github=GitHubCollector(
            timeout_seconds=settings.http_timeout_seconds,
            user_agent=settings.http_user_agent,
            api_token=_secret_value(settings.github_api_token),
        ),
        reddit=RedditCollector(
            timeout_seconds=settings.http_timeout_seconds,
            user_agent=settings.http_user_agent,
            client_id=_secret_value(settings.reddit_client_id),
            client_secret=_secret_value(settings.reddit_client_secret),
        ),
        youtube=YouTubeCollector(
            timeout_seconds=settings.http_timeout_seconds,
            user_agent=settings.http_user_agent,
            api_key=_secret_value(settings.youtube_api_key),
        ),
    )
    return DigestService(
        settings=settings,
        repository=repository,
        collector=collector,
        enricher=enricher,
        notifier=notifier,
    )


def build_feedback_poller(
    settings: Settings, repository: Repository
) -> TelegramFeedbackPoller | None:
    if settings.dry_run or not settings.telegram_feedback_enabled or not settings.telegram_enabled:
        return None
    token = _secret_value(settings.telegram_bot_token)
    if not token or settings.telegram_chat_id is None:
        raise ConfigurationError("Telegram credentials are missing for feedback polling")
    return TelegramFeedbackPoller(
        bot_token=token,
        chat_id=settings.telegram_chat_id,
        store=repository,
        api_base=settings.telegram_api_base,
        timeout_seconds=settings.http_timeout_seconds,
    )


def _secret_value(secret: object | None) -> str:
    if secret is None:
        return ""
    getter = getattr(secret, "get_secret_value", None)
    return str(getter() if getter is not None else secret).strip()


def _is_private_chat_id(value: str | None) -> bool:
    return value is not None and value.isdigit() and int(value) > 0
