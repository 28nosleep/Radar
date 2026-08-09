from __future__ import annotations

import json
from pathlib import Path

import pytest

from f117.config import Settings
from f117.services.runtime import ConfigurationError, validate_settings


def _catalog(tmp_path: Path, *, duplicate: bool = False) -> Path:
    path = tmp_path / "feeds.json"
    rows = [
        {
            "key": "source",
            "name": "Source",
            "feed_url": "https://example.com/feed",
        }
    ]
    if duplicate:
        rows.append(dict(rows[0]))
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_default_dry_run_requires_no_paid_credentials(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, rss_catalog_path=_catalog(tmp_path))

    validate_settings(settings)


def test_real_delivery_requires_both_providers(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rss_catalog_path=_catalog(tmp_path),
        dry_run=False,
    )

    with pytest.raises(ConfigurationError) as error:
        validate_settings(settings)

    assert "OpenAI must be enabled" in str(error.value)
    assert "Telegram must be enabled" in str(error.value)


def test_real_delivery_accepts_private_chat_and_credentials(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rss_catalog_path=_catalog(tmp_path),
        dry_run=False,
        openai_enabled=True,
        openai_api_key="test-openai",
        telegram_enabled=True,
        telegram_bot_token="test-telegram",
        telegram_chat_id="123456",
    )

    validate_settings(settings)


def test_catalog_rejects_duplicate_source_keys(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rss_catalog_path=_catalog(tmp_path, duplicate=True),
    )

    with pytest.raises(ConfigurationError, match="source keys must be unique"):
        validate_settings(settings)
