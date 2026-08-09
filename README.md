# Radar — Intelligence Engine

A personal intelligence feed for one owner. The current M4 milestone is a fully
working vertical slice:

`RSS / Hacker News / arXiv / GitHub / Reddit / YouTube → normalization → classification → deduplication → rule-based TOP-10 → OpenAI → personal Telegram`

The project never publishes anything to a channel. Telegram accepts only a positive
numeric `chat_id`, meaning a personal chat; negative group and channel IDs are rejected.

## Simplified roadmap

Every milestone ends with a runnable system, tests, a migration, and documentation.

1. **M1 — RSS vertical slice (complete).** A fixed JSON RSS catalog, a shared model,
   conservative deduplication, keyword categories, an explainable score, up to TOP-10
   OpenAI requests, and Telegram/dry-run delivery.
2. **M2 — Hacker News and arXiv (complete).** Two free collectors use the same model;
   the entire downstream pipeline remains unchanged.
3. **M3 — GitHub, Reddit, and YouTube (complete).** API collectors and metric snapshots
   are added. X is not part of this milestone.
4. **M4 — Discovery (current).** Star/upvote/comment/view deltas, growth velocity,
   a cross-source signal, and hidden findings — without ML or GPT.
5. **M5 — Personal tuning.** Telegram feedback, manual adjustment of weights and the
   catalog, and compact CLI status output. No automatic learning or heavy admin panel.

## M4 architecture

This is a modular monolith with one scheduler process. Queues, Redis, Celery/ARQ,
FastAPI, and separate services are unnecessary. Runs do not overlap; sources within a
single run are fetched asynchronously with a configurable limit.

The main data contract is:

`CollectedItem → NormalizedItem → StoredMaterial → RankedMaterial → EditorialCard`

- `adapters/` — RSS, Hacker News, arXiv, OpenAI Responses API, and Telegram Bot API;
- `pipeline/` — pure deterministic algorithms;
- `storage/` — SQLAlchemy/PostgreSQL and run/delivery history;
- `services/` — one application workflow;
- `cli/` — one-off runs, scheduler, configuration checks, and status.

OpenAI receives only the already-ranked TOP-N (`10` by default). Its result is stored
before Telegram delivery. Enriched but undelivered cards form a separate FIFO retry
queue and are always handled before new materials; no GPT request is spent again. If
OpenAI fails, real delivery of that card is postponed to the next run, while dry-run
prints a local fallback.

## Quick start

Docker and Docker Compose are required.

```bash
cp .env.example .env
docker compose build
docker compose up -d postgres
docker compose run --rm app alembic upgrade head
docker compose run --rm app radar validate-config
docker compose run --rm app radar run-once
```

Safe `dry-run` is enabled by default: OpenAI and Telegram are not called, the prepared
digest is printed to stdout, and materials are not marked as delivered.

For real personal delivery, `.env` needs:

```dotenv
F117_DRY_RUN=false
F117_OPENAI_ENABLED=true
F117_OPENAI_API_KEY=...
F117_TELEGRAM_ENABLED=true
F117_TELEGRAM_BOT_TOKEN=...
F117_TELEGRAM_CHAT_ID=123456789
```

After verification, start the persistent process:

```bash
docker compose up -d
docker compose logs -f app
```

The default interval is 180 minutes. All nonessential numbers, including TOP-N,
deduplication threshold, concurrency, and score weights, are defined in `.env.example`.

## Source catalog

Sources live in `config/feeds.json`. Each has a stable `key`, name, URL, `0..1`
reputation, enabled flag, and default categories. Removing a source from the catalog
disables it in the database while preserving its history.

RSS/Atom uses ETag/Last-Modified. Hacker News uses the official Firebase API; it
collects `top` by default (`new` or `best` can be selected in configuration) and 30
stories with points/comments. arXiv uses the public Atom API and reads `cs.AI`, `cs.LG`,
`cs.CL`, `cs.CV`, `cs.RO`, and `eess.SY` by default. Failure of one source does not stop
the others.

GitHub searches configured queries for new repositories and stores stars, forks,
language, topics, and the latest release. Reddit collects approved subreddits with
score/comments and post text; the catalog assigns a lower weight to `r/OpenAI`,
`r/ControlProblem`, `r/technology`, and `r/ChatGPT`. YouTube uses the Data API for
configured channels and search queries. It is enabled only after setting
`F117_YOUTUBE_API_KEY` and changing the source in `config/feeds.json` to `enabled: true`.

## Metric history

For GitHub, Reddit, and YouTube, every material seen again receives a snapshot in
`metric_snapshots`. Two snapshots are enough to calculate `growth_absolute`,
`growth_percent`, `growth_per_hour`, and the measurement window. This is a normal
ranking signal rather than the Discovery Engine: anomaly detection and separate alerts
remain M4 work. The editorial Telegram line `Trending: +…% over … h` appears only when
two snapshots exist.

You can optionally add `F117_GITHUB_API_TOKEN` (for a higher rate limit) and
`F117_YOUTUBE_API_KEY` to `.env`. Keys are never committed.

## Discovery Engine

M4 does not replace the importance score: the main ranking still answers “how important
is this material?”, while a separate `discovery_score` answers “how interesting is its
appearance or growth right now?”. It uses only existing snapshots: meaningful absolute
growth, percent/per-hour growth, acceleration with three snapshots, freshness, novelty,
and the number of independent sources. Percent growth from a small baseline is limited
by configurable `F117_DISCOVERY_MIN_BASELINE` and `F117_DISCOVERY_MIN_GROWTH_ABSOLUTE`.

Discovery only modestly boosts candidate selection through
`F117_DISCOVERY_SELECTION_BOOST`; it does not displace ordinary importance ranking.
Early materials with strong growth and moderate absolute popularity enter “Hidden
findings”. Telegram shows only confirmed growth signals or independent mentions;
formulas and discovery score are available only in debug mode.

When Reddit OAuth credentials are absent, all Reddit sources are skipped normally with
an informational log. This is not a run error and does not block RSS, HN, arXiv, GitHub,
or YouTube.

## Telegram

`F117_TELEGRAM_FORMAT=editorial` is the default: each card contains a section, Russian
title, short summary, “Why it matters” line, source, tags, and link. Service scores and
formula breakdowns are not included in the message. For diagnostics, set
`F117_TELEGRAM_FORMAT=debug`; those details are then appended to the card.

## Deduplication and score

Deduplication first checks source/external ID and canonical URL, then exact normalized
content/title. Fuzzy merging requires at least six words, `0.92` similarity, and a
three-day window. When in doubt, publications remain separate.

The explainable `0..100` score includes freshness, reputation, independent mentions,
popularity, growth velocity, novelty, topic affinity, and unusualness. RSS metrics are
usually sparse; full popularity/growth data comes from the M3 API collectors.

## Local verification

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/mypy f117
.venv/bin/pytest
```

Apply migrations with `.venv/bin/alembic upgrade head`; `radar status` shows database
state.

The product name and primary CLI command are `radar`. The internal Python package and
`F117_` variable prefix are retained for M1 compatibility; the `f117` alias continues to
work, but documentation and Docker use `radar`.
