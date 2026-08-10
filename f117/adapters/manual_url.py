from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import aiohttp


class UnsafeManualURL(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ManualPage:
    requested_url: str
    final_url: str
    title: str
    description: str
    source_name: str
    published_at: datetime | None = None
    author: str | None = None
    fetch_error: str | None = None


class SafeManualURLFetcher:
    """Fetch bounded public HTML while validating every redirect against SSRF."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        redirect_limit: int,
        max_response_bytes: int,
        user_agent: str,
    ) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.redirect_limit = redirect_limit
        self.max_response_bytes = max_response_bytes
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
        }

    async def fetch(self, url: str) -> ManualPage:
        requested = url.strip()
        current = requested
        try:
            connector = aiohttp.TCPConnector(resolver=_PublicResolver(), ttl_dns_cache=0)
            async with aiohttp.ClientSession(
                timeout=self.timeout, headers=self.headers, connector=connector
            ) as session:
                for redirect_count in range(self.redirect_limit + 1):
                    await validate_public_url(current)
                    async with session.get(current, allow_redirects=False) as response:
                        if 300 <= response.status < 400:
                            location = response.headers.get("Location")
                            if not location:
                                raise RuntimeError("redirect response has no Location")
                            if redirect_count >= self.redirect_limit:
                                raise RuntimeError("manual URL redirect limit exceeded")
                            current = urljoin(current, location)
                            continue
                        if response.status >= 400:
                            raise RuntimeError(f"source returned HTTP {response.status}")
                        content_type = response.headers.get("Content-Type", "").casefold()
                        if not any(
                            allowed in content_type
                            for allowed in ("text/html", "application/xhtml+xml", "text/plain")
                        ):
                            raise RuntimeError("manual URL is not a readable text page")
                        body = await _read_bounded(response, self.max_response_bytes)
                        parser = _MetadataParser()
                        parser.feed(body.decode(response.charset or "utf-8", errors="replace"))
                        return parser.page(requested, current)
                raise AssertionError("bounded redirect loop exhausted")
        except UnsafeManualURL:
            raise
        except (aiohttp.ClientError, TimeoutError, UnicodeError, RuntimeError) as exc:
            parsed = urlsplit(current)
            host = parsed.hostname or "unknown source"
            return ManualPage(
                requested_url=requested,
                final_url=current,
                title=_fallback_title(current),
                description="",
                source_name=host,
                fetch_error=str(exc),
            )


async def validate_public_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeManualURL("invalid URL") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise UnsafeManualURL("only absolute http/https URLs are accepted")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeManualURL("URL credentials are not accepted")
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise UnsafeManualURL("local/internal hostnames are blocked")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            addresses = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                port or (443 if parsed.scheme.casefold() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise RuntimeError(f"hostname resolution failed: {exc}") from exc
        resolved = {ipaddress.ip_address(item[4][0]) for item in addresses}
    else:
        resolved = {literal}
    if not resolved or any(not address.is_global for address in resolved):
        raise UnsafeManualURL("localhost/private/internal addresses are blocked")


class _PublicResolver(aiohttp.abc.AbstractResolver):
    """Pin each HTTP connection to addresses checked immediately by the resolver."""

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC
    ) -> list[aiohttp.abc.ResolveResult]:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM
        )
        results: list[aiohttp.abc.ResolveResult] = []
        for resolved_family, _, proto, _, address in infos:
            ip = ipaddress.ip_address(address[0])
            if not ip.is_global:
                raise UnsafeManualURL("DNS resolved to a localhost/private/internal address")
            results.append(
                {
                    "hostname": host,
                    "host": str(ip),
                    "port": port,
                    "family": resolved_family,
                    "proto": proto,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not results:
            raise OSError(f"hostname resolution returned no addresses for {host}")
        return results

    async def close(self) -> None:
        return None


async def _read_bounded(response: aiohttp.ClientResponse, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(16_384):
        size += len(chunk)
        if size > limit:
            raise RuntimeError("manual URL response exceeds the size limit")
        chunks.append(chunk)
    return b"".join(chunks)


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs if value is not None}
        if tag.casefold() == "title":
            self.in_title = True
        elif tag.casefold() == "meta":
            key = (values.get("property") or values.get("name") or "").casefold()
            content = (values.get("content") or "").strip()
            if key and content and key not in self.metadata:
                self.metadata[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    def page(self, requested_url: str, final_url: str) -> ManualPage:
        title = self.metadata.get("og:title") or self.metadata.get("twitter:title")
        title = title or " ".join(self.title_parts)
        description = (
            self.metadata.get("og:description")
            or self.metadata.get("twitter:description")
            or self.metadata.get("description")
            or ""
        )
        published = self.metadata.get("article:published_time")
        return ManualPage(
            requested_url=requested_url,
            final_url=final_url,
            title=_clean(title) or _fallback_title(final_url),
            description=_clean(description),
            source_name=_clean(self.metadata.get("og:site_name", ""))
            or (urlsplit(final_url).hostname or "Manual source"),
            published_at=_parse_published(published),
            author=_clean(self.metadata.get("author", "")) or None,
        )


def _parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _fallback_title(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
    return _clean(path) or f"Ссылка с {parsed.hostname or 'unknown source'}"
