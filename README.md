# Radar — Intelligence Engine

A personal intelligence feed for one owner. The current M4 milestone is a fully
working vertical slice:

`RSS / Hacker News / arXiv / GitHub / Reddit / YouTube → normalization → classification → deduplication → editorial gate → OpenAI → personal Telegram`

The project never publishes anything to a channel. Telegram accepts only a positive
numeric `chat_id`, meaning a personal chat; negative group and channel IDs are rejected.

## Simplified roadmap

Every milestone ends with a runnable system, tests, a migration, and documentation.

1. **M1 — RSS vertical slice (complete).** A fixed JSON RSS catalog, a shared model,
   conservative deduplication, keyword categories, an explainable score, deterministic
   audience-interest selection, optional OpenAI enrichment, and Telegram/dry-run delivery.
2. **M2 — Hacker News and arXiv (complete).** Two free collectors use the same model;
   the entire downstream pipeline remains unchanged.
3. **M3 — GitHub, Reddit, and YouTube (complete).** API collectors and metric snapshots
   are added. X is not part of this milestone.
4. **M4 — Discovery (complete).** Star/upvote/comment/view deltas, growth velocity,
   a cross-source signal, and hidden findings — without ML or GPT.
5. **M5 — Quality & Feedback (current).** Telegram feedback, editorial diversity,
   source-quality and discovery-calibration reports. Feedback is stored for later
   review; it never changes ranking automatically.

## M5 architecture

This is a modular monolith with one scheduler process. Queues, Redis, Celery/ARQ,
FastAPI, and separate services are unnecessary. A PostgreSQL advisory lock gives the
complete run a single owner, including manual `run-once` invocations; sources within a
single owned run are fetched asynchronously with a configurable limit.

The main data contract is:

`CollectedItem → NormalizedItem → StoredMaterial → RankedMaterial → EditorialCard`

- `adapters/` — RSS, Hacker News, arXiv, OpenAI Responses API, and Telegram Bot API;
- `pipeline/` — pure deterministic algorithms;
- `storage/` — SQLAlchemy/PostgreSQL and run/delivery history;
- `services/` — one application workflow;
- `cli/` — one-off runs, scheduler, configuration checks, and status.

OpenAI receives only the small set that passed deterministic editorial and source gates
(`5` cards maximum per digest by default). Each successful result
is stored immediately before the next material is requested. Provider failures use a
bounded per-material retry state; malformed Telegram cards are quarantined rather than
blocking other cards. Enriched but undelivered cards form a separate FIFO retry queue
and are always handled before new materials; no GPT request is spent again. Telegram
uses a lease-backed pre-send claim, so a crash before the HTTP request can recover. A
possibly accepted Telegram request is held separately and is never resent automatically;
the owner may explicitly release it with `radar retry-delivery <material-uuid>`.

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

Safe `dry-run` is enabled by default: collectors, OpenAI, Telegram delivery, and Telegram
feedback polling are not called. The eligible locally stored digest is printed to stdout,
and materials are not marked as delivered.

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

Collection runs every 180 minutes by default. Regular Telegram delivery has its own three
daily windows (`09:00,15:00,21:00` in `Europe/Moscow`) and sends at most five worthy cards
per window. These are ceilings, not quotas: a digest can contain one card or none. An
exceptional urgent item can be delivered on a collection tick. Both clocks start in the
future after process startup, so a Docker restart does not itself emit a digest. All
thresholds and schedule values are defined in `.env.example`.

## Source catalog

Sources live in `config/feeds.json`. Each has a stable `key`, name, URL, `0..1`
reputation, enabled flag, and default categories. Removing a source from the catalog
disables it in the database while preserving its history.

RSS/Atom uses ETag/Last-Modified. Hacker News uses the official Firebase API; it
collects `top` by default (`new` or `best` can be selected in configuration) and 30
stories with points/comments. arXiv uses the public Atom API and reads `cs.AI`, `cs.LG`,
`cs.CL`, `cs.CV`, `cs.RO`, and `eess.SY` by default. It remains a discovery sensor, but
delivery requires a clear public-facing implication. Failure of one source does not stop
the others.

GitHub searches configured queries for new repositories and stores stars, forks,
language, topics, and the latest release. Small repositories remain in the database, but
delivery additionally requires adoption, velocity, independent mentions, a known-team
release, or an exceptional idea. Reddit collects approved subreddits with
score/comments and post text; the catalog assigns a lower weight to `r/OpenAI`,
`r/ControlProblem`, `r/technology`, and `r/ChatGPT`. YouTube uses the Data API for
configured channels and search queries. It is enabled only after setting
`F117_YOUTUBE_API_KEY` and changing the source in `config/feeds.json` to `enabled: true`.

Cyberculture is covered by the official feeds for `WIRED Culture`, The Verge
Entertainment/Culture, 404 Media, Polygon, and Futurism. They enter through the existing
RSS adapter with no blanket source boost: ordinary entertainment is rejected while
AI/cyberpunk/robots/hacking/transhumanism and technologically significant internet culture
can qualify as `CYBERCULTURE`.

Reddit uses the existing API-or-RSS collector. The cultural sensors are
`r/Cyberpunk`, `r/scifi`, `r/transhumanism`, `r/InternetIsBeautiful`, `r/LV426`, and
`r/bladerunner`; each public RSS source is merged across `new`, `hot`, and `rising` by
post ID. RSS requests share a small global spacing/jitter policy, honor `Retry-After`,
and use bounded exponential backoff for HTTP 429. A failed listing is logged and does
not abort the collection cycle. `r/LV426` and `r/bladerunner` are cultural sensors:
fan art, cosplay, merchandise, lore questions, nostalgia, and generic fandom chatter
are rejected even when the franchise is relevant.

## Metric history

For GitHub, Reddit, YouTube, and Hacker News, every material seen again receives a
snapshot in `metric_snapshots`. Provider-namespaced metrics are aggregated onto a
canonical duplicate root without summing crossposts from the same provider family.
Two snapshots calculate an absolute per-hour signal and measurement window; corrections
or negative latest deltas clear a stale rising signal. This remains a normal ranking
signal rather than an alerting system. The editorial Telegram line appears only when two
snapshots confirm positive growth.

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

When Reddit OAuth credentials are absent, Reddit falls back to the public RSS pipeline
(`new`, `hot`, `rising`) with the same semantic event gate. This is not a run error and
does not block RSS, HN, arXiv, GitHub, or YouTube. OAuth remains the preferred path when
credentials are configured.

## Telegram

`F117_TELEGRAM_FORMAT=editorial` is the default: each card contains a section, Russian
title, short summary, “Why it matters” line, source, tags, and link. Service scores and
formula breakdowns are not included in the message. For diagnostics, set
`F117_TELEGRAM_FORMAT=debug`; those details are then appended to the card.

Every real editorial card also has three inline buttons: `👍 Полезно`, `👎 Мимо`, and
`⭐ В пост`. The bot accepts callbacks only from the configured personal chat. One latest
verdict is stored per material; a later click replaces the previous verdict rather than
creating a duplicate. The scheduler polls callbacks every 10 seconds by default; use
`radar poll-feedback` for one safe manual pass. Feedback is deliberately not used to
change ranking in M5.

## Quality and calibration reports

Use these local CLI reports after collecting some real history:

```bash
radar quality-report --days 7
radar discovery-report --days 7
```

The quality report breaks down collected, TOP-selected, sent, useful, miss, and post-fit
materials per source, plus average importance and discovery scores. The discovery report
shows distributions of importance score, discovery score, and growth per hour, together
with current rising and hidden-gem candidates. It explicitly reports when the data is too
small for meaningful calibration.

Before editorial enrichment, a soft diversity layer prefers a mix of sources, recognized
companies/projects, and categories. Default caps are two cards per source, two per known
entity, and four per category. If no equally worthy alternative exists—or a material's
score reaches the configured strong threshold—the material is still included. Cached,
already-enriched retry cards retain their delivery priority.

## Deduplication and score

Deduplication first checks source/external ID and canonical URL, then exact normalized
content/title. Fuzzy merging requires at least six words, `0.92` similarity, and a
three-day window. When in doubt, publications remain separate.

The original explainable `0..100` importance score still includes freshness, reputation,
independent mentions, popularity, growth velocity, novelty, topic affinity, and
unusualness. A separate deterministic `editorial_fit` asks whether id:28's audience would
open the story. Delivery uses a weighted score (65% editorial fit by default), plus hard
minimums for editorial fit and the combined delivery score. Enterprise minutiae,
specialist-only papers, weak GitHub projects, and generic entertainment cannot buy a slot
with technical relevance or popularity alone. Entity names such as OpenAI, Anthropic,
Google DeepMind, xAI, Meta, and NVIDIA are supporting signals, not proof of a major event:
courses, policy/evaluation posts, system-card addenda, benchmark methodology, and generic
documentation do not receive major-event treatment automatically. RSS metrics are usually
sparse; full popularity/growth data comes from the M3 API collectors.

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
