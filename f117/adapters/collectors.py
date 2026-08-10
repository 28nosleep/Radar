from __future__ import annotations

from dataclasses import replace

from f117.adapters.arxiv import ArxivCollector
from f117.adapters.github import GitHubCollector
from f117.adapters.hacker_news import HackerNewsCollector
from f117.adapters.reddit import RedditCollector
from f117.adapters.rss import FeedFetchResult, RSSCollector
from f117.adapters.youtube import YouTubeCollector
from f117.domain import FeedSource


class SourceCollector:
    """Route fixed source configurations to their dedicated adapters."""

    def __init__(
        self,
        *,
        rss: RSSCollector,
        hacker_news: HackerNewsCollector,
        arxiv: ArxivCollector,
        github: GitHubCollector,
        reddit: RedditCollector,
        youtube: YouTubeCollector,
    ) -> None:
        self.rss = rss
        self.hacker_news = hacker_news
        self.arxiv = arxiv
        self.github = github
        self.reddit = reddit
        self.youtube = youtube

    async def fetch(
        self, source: FeedSource, *, etag: str | None = None, last_modified: str | None = None
    ) -> FeedFetchResult:
        if source.kind == "rss":
            result = await self.rss.fetch(source, etag=etag, last_modified=last_modified)
        elif source.kind == "hacker_news":
            result = await self.hacker_news.fetch(source, etag=etag, last_modified=last_modified)
        elif source.kind == "arxiv":
            result = await self.arxiv.fetch(source, etag=etag, last_modified=last_modified)
        elif source.kind == "github":
            result = await self.github.fetch(source, etag=etag, last_modified=last_modified)
        elif source.kind == "reddit":
            result = await self.reddit.fetch(source, etag=etag, last_modified=last_modified)
        else:
            result = await self.youtube.fetch(source, etag=etag, last_modified=last_modified)
        signal = f"source_role:{source.role.value}"
        return replace(
            result,
            items=[
                item.model_copy(
                    update={
                        "qualitative_signals": sorted(set(item.qualitative_signals).union({signal}))
                    }
                )
                for item in result.items
            ],
        )
