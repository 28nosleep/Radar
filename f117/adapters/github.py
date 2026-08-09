from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from f117.adapters.rss import FeedFetchResult
from f117.domain import Category, CollectedItem, FeedSource


class GitHubFetchError(RuntimeError):
    pass


class GitHubCollector:
    def __init__(self, *, timeout_seconds: float, user_agent: str, api_token: str = "") -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.headers = {"Accept": "application/vnd.github+json", "User-Agent": user_agent}
        if api_token:
            self.headers["Authorization"] = f"Bearer {api_token}"

    async def fetch(
        self,
        source: FeedSource,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> FeedFetchResult:
        del etag, last_modified
        if not source.github_queries:
            raise GitHubFetchError(f"{source.key} must declare github_queries")
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession(timeout=self.timeout, headers=self.headers)
        try:
            since = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()
            per_query_limit = max(1, -(-source.item_limit // len(source.github_queries)))
            searches = await asyncio.gather(
                *(
                    self._json(
                        session,
                        f"{str(source.feed_url).rstrip('/')}/search/repositories",
                        params={
                            "q": f"{query} created:>={since}",
                            "sort": "updated",
                            "order": "desc",
                            "per_page": str(per_query_limit),
                        },
                    )
                    for query in source.github_queries
                ),
                return_exceptions=True,
            )
            repositories: dict[int, dict[str, Any]] = {}
            ordered_ids: list[int] = []
            query_items = [
                payload.get("items", []) if isinstance(payload, dict) else []
                for payload in searches
            ]
            for position in range(per_query_limit):
                for items in query_items:
                    if position >= len(items):
                        continue
                    repo = items[position]
                    if isinstance(repo, dict) and isinstance(repo.get("id"), int):
                        repositories[repo["id"]] = repo
                        if repo["id"] not in ordered_ids:
                            ordered_ids.append(repo["id"])
            if not repositories:
                raise GitHubFetchError(f"All GitHub searches failed for {source.key}")
            # Round-robin query result order makes every configured query visible
            # before any one broad query can fill the source limit.
            selected = [repositories[repo_id] for repo_id in ordered_ids[: source.item_limit]]
            releases: dict[str, dict[str, Any] | None] = {}
            if source.github_include_releases:
                release_results = await asyncio.gather(
                    *(self._latest_release(session, repo) for repo in selected),
                    return_exceptions=True,
                )
                releases = {
                    str(repo.get("full_name")): release if isinstance(release, dict) else None
                    for repo, release in zip(selected, release_results, strict=True)
                }
            collected_at = datetime.now(UTC)
            return FeedFetchResult(
                items=[
                    self._repo_to_item(
                        source, repo, collected_at, releases.get(str(repo.get("full_name")))
                    )
                    for repo in selected
                ],
                etag=None,
                last_modified=None,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise GitHubFetchError(f"Failed to fetch {source.key}: {exc}") from exc
        finally:
            if owns_session:
                await session.close()

    async def _json(
        self, session: aiohttp.ClientSession, url: str, *, params: dict[str, str] | None = None
    ) -> object:
        async with session.get(url, params=params, headers=self.headers) as response:
            if response.status != 200:
                raise GitHubFetchError(f"GitHub returned HTTP {response.status}")
            return await response.json(content_type=None)

    async def _latest_release(
        self, session: aiohttp.ClientSession, repo: dict[str, Any]
    ) -> dict[str, Any] | None:
        full_name = repo.get("full_name")
        if not isinstance(full_name, str):
            return None
        async with session.get(
            f"https://api.github.com/repos/{full_name}/releases/latest", headers=self.headers
        ) as response:
            if response.status == 404:
                return None
            if response.status != 200:
                raise GitHubFetchError(f"GitHub release lookup returned HTTP {response.status}")
            payload = await response.json(content_type=None)
            return payload if isinstance(payload, dict) else None

    @staticmethod
    def _repo_to_item(
        source: FeedSource,
        repo: dict[str, Any],
        collected_at: datetime,
        release: dict[str, Any] | None,
    ) -> CollectedItem:
        repo_id = repo["id"]
        owner_value = repo.get("owner")
        owner: dict[str, Any] = dict(owner_value) if isinstance(owner_value, dict) else {}
        topics = [str(topic) for topic in repo.get("topics", []) if isinstance(topic, str)]
        release_name = str(release.get("name") or release.get("tag_name") or "") if release else ""
        description = str(repo.get("description") or "")
        if release_name:
            description = f"{description}\nПоследний релиз: {release_name}".strip()
        return CollectedItem(
            external_id=str(repo_id),
            source_key=source.key,
            source_name=source.name,
            source_reputation=source.reputation,
            title=str(repo.get("full_name") or repo.get("name")),
            url=str(repo.get("html_url")),
            published_at=_parse_github_date(repo.get("updated_at") or repo.get("created_at")),
            collected_at=collected_at,
            description=description,
            author=str(owner.get("login") or "").strip() or None,
            source_categories=[*source.default_categories, Category.OPEN_SOURCE],
            popularity={
                "github_stars": float(repo.get("stargazers_count") or 0),
                "forks": float(repo.get("forks_count") or 0),
                "releases": float(bool(release)),
            },
            raw={"language": repo.get("language"), "topics": topics, "release": release_name},
        )


def _parse_github_date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
