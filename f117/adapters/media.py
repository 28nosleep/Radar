"""Bounded, final-candidate-only Open Graph image lookup."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import aiohttp


class MetadataMediaFetcher:
    def __init__(self, *, timeout_seconds: float, max_response_bytes: int, user_agent: str) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self.headers = {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"}

    async def image_for(self, url: str) -> str | None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        try:
            async with aiohttp.ClientSession(timeout=self.timeout, headers=self.headers) as session:
                async with session.get(url, allow_redirects=True, max_redirects=3) as response:
                    if response.status != 200 or not response.content_type.startswith("text/html"):
                        return None
                    if (
                        response.content_length
                        and response.content_length > self.max_response_bytes
                    ):
                        return None
                    body = await self._read_bounded(response)
        except (aiohttp.ClientError, TimeoutError):
            return None
        match = _IMAGE_META_RE.search(body.decode("utf-8", errors="ignore"))
        if match is None:
            return None
        candidate = match.group(1).strip()
        parsed_candidate = urlsplit(candidate)
        return (
            candidate
            if parsed_candidate.scheme in {"http", "https"} and parsed_candidate.netloc
            else None
        )

    async def _read_bounded(self, response: aiohttp.ClientResponse) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.content.iter_chunked(32 * 1024):
            size += len(chunk)
            if size > self.max_response_bytes:
                return b""
            chunks.append(chunk)
        return b"".join(chunks)


_IMAGE_META_RE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:og:image|twitter:image)[\"'][^>]+content=[\"']([^\"']+)",
    re.IGNORECASE,
)
