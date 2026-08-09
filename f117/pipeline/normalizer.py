"""Deterministic normalization for collected feed items."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from f117.domain import CollectedItem, NormalizedItem

_TRACKING_QUERY_KEYS = frozenset(
    {
        "_hsenc",
        "_hsmi",
        "dclid",
        "fbclid",
        "gclid",
        "gbraid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "mkt_tok",
        "msclkid",
        "oly_anon_id",
        "oly_enc_id",
        "rb_clickid",
        "twclid",
        "vero_conv",
        "vero_id",
        "wbraid",
    }
)
_SKIPPED_HTML_ELEMENTS = frozenset({"script", "style", "noscript", "template"})
_BLOCK_HTML_ELEMENTS = frozenset(
    {
        "article",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_WHITESPACE_RE = re.compile(r"\s+")
_SAFE_HOST_ALIASES = frozenset(
    {
        "arxiv.org",
        "github.com",
        "news.ycombinator.com",
        "reddit.com",
        "www.youtube.com",
        "youtube.com",
    }
)


class _TextExtractor(HTMLParser):
    """Small dependency-free HTML-to-text extractor for RSS fragments."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized_tag = tag.casefold()
        if normalized_tag in _SKIPPED_HTML_ELEMENTS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and normalized_tag in _BLOCK_HTML_ELEMENTS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() in _SKIPPED_HTML_ELEMENTS:
            self._skip_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _SKIPPED_HTML_ELEMENTS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._skip_depth == 0 and normalized_tag in _BLOCK_HTML_ELEMENTS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def clean_html(value: str | None) -> str:
    """Return readable plain text from a short HTML or text fragment."""

    if not value:
        return ""

    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        extracted = "".join(parser.parts)
    except Exception:
        # Malformed publisher HTML must not stop the ingestion pipeline.
        extracted = re.sub(r"<[^>]*>", " ", value)

    normalized = unicodedata.normalize("NFKC", unescape(extracted))
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def normalize_title(value: str) -> str:
    """Build a stable, human-language-independent title comparison key."""

    cleaned = clean_html(value).casefold()
    characters = (
        character if unicodedata.category(character)[0] in {"L", "N"} else " "
        for character in cleaned
    )
    return _WHITESPACE_RE.sub(" ", "".join(characters)).strip()


def _is_tracking_query_key(raw_key: str) -> bool:
    key = unquote_plus(raw_key).casefold().strip()
    return key.startswith("utm_") or key in _TRACKING_QUERY_KEYS


def _remove_tracking_query_parameters(query: str) -> str:
    """Remove trackers without decoding, sorting, or rewriting useful values."""

    if not query:
        return ""
    retained: list[str] = []
    for component in query.split("&"):
        raw_key = component.partition("=")[0]
        if not _is_tracking_query_key(raw_key):
            retained.append(component)
    return "&".join(retained)


def normalize_url(value: str) -> str:
    """Canonicalize an HTTP(S) article URL conservatively.

    Query order and non-tracking parameters are kept because changing either can
    alter the referenced resource. A tiny allow-list normalizes known HTTP(S)/www
    aliases and trailing slashes used by stable content hosts.
    """

    raw_url = unescape(value).strip()
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid article URL: {value!r}") from exc

    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"article URL must be absolute HTTP(S): {value!r}")

    hostname = parsed.hostname.rstrip(".").casefold()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid hostname in article URL: {value!r}") from exc

    if hostname.startswith("www.") and hostname[4:] in _SAFE_HOST_ALIASES:
        hostname = hostname[4:]
    if hostname in _SAFE_HOST_ALIASES:
        scheme = "https"
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += f":{parsed.password}"
        userinfo += "@"

    is_default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    port_suffix = "" if port is None or is_default_port else f":{port}"
    netloc = f"{userinfo}{host_for_netloc}{port_suffix}"
    path = parsed.path or "/"
    if hostname in _SAFE_HOST_ALIASES and path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = _remove_tracking_query_parameters(parsed.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def ensure_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime; naive feed timestamps are assumed UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_item(item: CollectedItem) -> NormalizedItem:
    """Convert a collector result into the common deterministic representation."""

    title = clean_html(item.title)
    if not title:
        raise ValueError("collected item title must not be empty after normalization")

    description = clean_html(item.description)
    normalized_title = normalize_title(title)
    collected_at = ensure_utc(item.collected_at)
    published_at = ensure_utc(item.published_at or item.collected_at)
    canonical_url = normalize_url(item.url)
    normalized_description = normalize_title(description)
    hash_input = f"{normalized_title}\n{normalized_description}".encode()

    return NormalizedItem(
        external_id=item.external_id.strip(),
        source_key=item.source_key,
        source_name=clean_html(item.source_name),
        source_reputation=item.source_reputation,
        title=title,
        url=item.url.strip(),
        canonical_url=canonical_url,
        published_at=published_at,
        collected_at=collected_at,
        description=description,
        author=clean_html(item.author) or None,
        source_categories=list(item.source_categories),
        categories=[],
        popularity=dict(item.popularity),
        content_hash=sha256(hash_input).hexdigest(),
        normalized_title=normalized_title,
    )
