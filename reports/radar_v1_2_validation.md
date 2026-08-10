# Radar v1.2 validation report

Validation date: 2026-08-10. All live checks were read-only with respect to Telegram;
no production Telegram message was sent.

## 1. Result

Radar remains a modular monolith on PostgreSQL and Docker Compose. The existing
collection, deterministic ranking, editorial enrichment, delivery, and feedback
lifecycle is preserved. The change narrows the product toward a personal editor:

- Reddit is optional and disabled by default;
- an owner can send a URL to the private Telegram bot for manual evaluation;
- local EN→RU translation happens only after filtering;
- OpenAI produces a critical structured verdict instead of translating or forcing a
  positive explanation;
- automatic `SKIP` cards are withheld, while manual submissions always receive an
  answer;
- the Telegram card has a neutral description and `Что думает AI`, with the existing
  feedback buttons.

The additive migration keeps legacy enrichment JSON/columns intact but application code
does not read or generate the retired commentary fields.

## 2. Primary-source validation

Endpoints were checked directly on 2026-08-10. Only official domains were considered;
no third-party feed generators or source-specific scrapers were added.

| Requested source | Official endpoint checked | Result | Radar status |
|---|---|---:|---|
| Meta AI | `https://engineering.fb.com/category/ai-research/feed/` | HTTP 200, RSS | Added as `Meta Engineering: AI Research` |
| Boston Dynamics | `https://bostondynamics.com/feed/` | HTTP 200, RSS | Added |
| Anthropic | `/news/rss.xml`, `/rss.xml` on `anthropic.com` | HTTP 404 | Unavailable for automatic RSS |
| Meta AI Blog | `https://ai.meta.com/blog/rss/` | HTTP 404 | Blog feed unavailable; official Engineering AI feed used |
| xAI | `/news/rss.xml`, `/rss.xml` on `x.ai` | HTTP 403; no confirmable official feed | Unavailable without scraping |
| Figure AI | `https://www.figure.ai/news/rss.xml` | HTTP 404 | Unavailable without scraping |

The existing OpenAI, Google DeepMind, NVIDIA, MIT AI, WIRED Culture, The Verge, 404
Media, Futurism, Polygon, Hacker News, YouTube, GitHub, and arXiv sources remain.
Sources now carry a conceptual `PRIMARY_NEWS` or `DISCOVERY` role. Hacker News, GitHub,
arXiv, YouTube, and Reddit default to discovery; their activity is not treated as equal
to a major first-party announcement. Existing strict GitHub/arXiv delivery gates remain,
and no global quality threshold was lowered.

## 3. Reddit status

`F117_REDDIT_COLLECTION_ENABLED=false` is the default. Disabled Reddit sources are
removed from the active collector set before a run, without affecting other sources.
If explicitly enabled, the existing RSS collector and semantic gate are used as-is;
HTTP 429 and other source failures are logged per source and do not fail the collection
cycle. No proxy, unofficial API, scraping, or rate-limit workaround was added.

A live collection smoke after the migration completed successfully with Reddit disabled:

- run: `2c2631f0-9989-4720-9c60-414842985820`;
- status: `completed`;
- collected: 284;
- inserted: 26;
- source failures: none;
- Telegram delivery: not invoked.

## 4. Manual Share → Radar smoke

The private bot now recognizes an ordinary owner message containing an HTTP(S) URL.
The manual fetcher validates the scheme, forbids credentials, resolves and pins a public
IP for each connection, revalidates every redirect, blocks loopback/private/link-local/
internal destinations, and enforces timeout, redirect, and response-size limits.

A live smoke used the official Boston Dynamics Atlas page. The first intake created or
reused material `716a18e0-303d-4371-beab-9e075a662a81`; the second normalized URL returned
the same material with `duplicate=true`. Metadata fetched without error, the result was
`INTERESTING`, and the card rendered locally with feedback buttons. Telegram sending was
not called.

Reddit URLs use this same safe path. If public metadata/title/outbound URL is all that is
available, Radar evaluates that evidence and says when the content is insufficient; it
does not bypass Reddit protection.

## 5. Local translation

The `TranslationProvider` abstraction has a `LocalTranslationProvider` backed by the
Compose `libretranslate/libretranslate:v1.9.6` service, limited to English and Russian.
Inputs are bounded, calls have a timeout, requests are serialized, and translations are
cached by content hash in PostgreSQL. A translator failure falls back to bounded English
text. URLs, numbers, and important brand/model tokens are protected during translation.

Translation wraps only deterministic finalists and manual submissions. It does not
pre-translate the article corpus. OpenAI is not a translation provider and receives no
standalone translation calls.

Ten live EN→RU checks against the local container:

| Kind | Source English | Local Russian result |
|---|---|---|
| AI | OpenAI releases GPT-5 with improved coding and reasoning capabilities. | OpenAI выпускает GPT-5 с улучшенными возможностями кодирования и рассуждения. |
| AI | Anthropic introduces a new Claude model for long-running agent tasks. | Anthropic представляет новую модель Claude для долгосрочных задач агента. |
| Robotics | Figure AI says its humanoid robot completed a full shift at a BMW factory. | Figure AI говорит, что его человекоподобный робот завершил полную смену на заводе BMW. |
| Robotics | Boston Dynamics demonstrates Atlas handling parts in an industrial workspace. | Boston Dynamics демонстрирует Atlas обработку деталей в промышленном рабочем пространстве. |
| Technical | NVIDIA reduced inference latency by 35% on the same GPU hardware. | NVIDIA уменьшает задержку вывода на 35% на том же GPU оборудовании. |
| Cyberculture | A viral synthetic video reignited debate about trust in online media. | Вирусное синтетическое видео возродило дебаты о доверии к онлайн-медиа. |
| GitHub | The GitHub repository has 1 star, 0 forks, and no recorded growth. | В хранилище GitHub есть звезда 1, вилки 0 и нет зарегистрированного роста. |
| Research | The arXiv paper reports a narrow benchmark gain without real-world evaluation. | В документе arXiv сообщается о узком бенчмарке без реальной оценки. |
| Technical | Read the API notes at https://example.com/docs/v2 before upgrading. | Прочтите заметки API в https://example.com/docs/v2 перед обновлением. |
| Research | Authors claim 92.4% accuracy, but the dataset contains only 500 examples. | Авторы утверждают 92.4% точность, но набор данных содержит только 500 примеры. |

All repeated calls returned the cached value. Names, URL, and numbers were preserved.
The grammar is deliberately utilitarian rather than literary; it is adequate for fast,
free comprehension.

## 6. Editorial output and Telegram card

The OpenAI schema is now `title_ru`, `summary_ru`, `ai_opinion`, `ai_verdict`, and
`post_fit_score`. The prompt receives source, title, factual excerpt, available metrics,
source reputation, cross-source/freshness/discovery signals, category, and manual status.
It explicitly prohibits inventing absent evidence and forced positive language.

`ai_opinion` must contain 2–4 complete sentences and about 250–600 characters. Validation
rejects ellipsis and unfinished punctuation. It first keeps only complete sentences and,
if that cannot produce a valid result, permits one bounded regeneration. It never hard
truncates in the middle of a sentence.

The rendered card contains:

1. category and Russian title;
2. neutral factual description;
3. `Что думает AI`;
4. source, tags, and original link;
5. `👍 Полезно`, `👎 Мимо`, `⭐ В пост`.

The old `Почему это важно` and personal-comment blocks are absent. `ironic_comment` is not
present in application runtime, prompt, output schema, or renderer.

Example from the manual Boston Dynamics smoke:

> Обратить внимание стоит: Atlas остаётся одним из наиболее заметных проектов
> человекоподобной робототехники. Но это корпоративное описание без конкретных данных о
> надёжности, темпе работы и независимой проверке; доказательств готовности к масштабу
> пока нет.

## 7. Weekly feedback KPI

The quality report now exposes delivered, useful, missed, saved, useful rate, save rate,
category breakdown, source breakdown, sources with most misses, and sources with most
saves. It does not train a personalization model.

Read-only seven-day snapshot from the existing database:

- materials: 1,789;
- delivered: 39;
- useful: 6 (`15.38%` of delivered);
- missed: 4;
- saved: 3 (`7.69%` of delivered);
- most misses: `github-ai-robotics` (3), `arxiv-ai` (1);
- most saves: `hacker-news` (2), legacy `reddit-singularity` (1).

The legacy Reddit delivery in this historical window predates the new default-off policy.

## 8. Read-only calibration

### Fresh deterministic finalists

The following candidates had already passed the deterministic eligibility gate. AI was
allowed to reject them; three of the ten became `SKIP`. The run did not claim or deliver
cards.

| Title | Source | Category | Freshness | Editorial fit | Importance | Delivery score | Verdict | Would deliver |
|---|---|---|---:|---:|---:|---:|---|---|
| Show HN: Voice driven murder mystery | Hacker News | AI/LLM | 8.7 h | 91 | 52.21 | 77.42 | INTERESTING | YES |
| Real-Time Underwater Image Processing System | Reddit: r/robotics | Robotics | 15.5 h | 75 | 39.08 | 62.43 | WEAK | YES |
| Palm-Sized Three-Wheel Omnidirectional Robot | Reddit: r/robotics | Robotics | 24.1 h | 75 | 36.81 | 61.63 | WEAK | YES |
| Underwater Image Processing — 4K 60FPS Part 2 | Reddit: r/robotics | Robotics/Hardware | 40.6 h | 75 | 33.41 | 60.44 | INTERESTING | YES |
| Auto mode is now the default in Claude Code | Hacker News | AI/LLM | 8.1 h | 63 | 55.29 | 60.30 | WEAK | YES |
| Ford AI assistant checks fuel and tire pressure | The Verge | AI | 1.0 h | 66 | 44.63 | 58.52 | WEAK | YES |
| Weird Town Begs for AI Data Center | Futurism | AI | 48.9 h | 71 | 31.30 | 57.10 | SKIP | NO |
| Docker Sandboxes for AI agents | Hacker News | AI | 5.9 h | 58 | 55.12 | 56.99 | INTERESTING | YES |
| The tragedy of the commons, AI edition | Hacker News | AI | 16.2 h | 58 | 50.23 | 55.28 | SKIP | NO |
| Philippines offshoring grows despite AI | Hacker News | AI | 7.4 h | 58 | 49.46 | 55.01 | SKIP | NO |

Representative rejection:

> Обращать внимание пока не на что: это скорее провокационный заголовок о локальной
> истории, чем материал о развитии ИИ-инфраструктуры. Без названия города, параметров
> проекта, источников финансирования и данных о последствиях для энергии, воды и
> занятости нельзя оценить ни новизну, ни практическую значимость.

### Controlled A–G cases

| Case | Expected shape | Result | Would deliver | Critical conclusion |
|---|---|---|---|---|
| A | Major OpenAI release | STRONG | YES | Immediate API availability plus three independent reproductions is a strong, testable signal. |
| B | GitHub, 1 star, no growth | SKIP | NO | No details, adoption, growth, release, or evidence of use. |
| C | Empty corporate marketing | HYPE | NO | Universal promise with no product detail, customer, measurement, or independent evidence. |
| D | Strong humanoid demo | INTERESTING | YES | Technically meaningful sequence, but controlled video gives no reliability or deployment evidence. |
| E | Sensational Futurism headline | HYPE | NO | Headline outruns an unreplicated exploratory study. |
| F | Important cyberculture policy | INTERESTING | YES | Concrete mandatory labels/provenance, independently confirmed; implementation details still missing. |
| G | Entertainment filler | SKIP | NO | No technology event, cybercultural shift, or broader consequence. |

This demonstrates that deterministic passage is not an automatic editorial endorsement.
`HYPE` is withheld unless the hype itself is culturally significant. Manual submissions
would still render any of the five verdicts represented by these seven cases, including
`SKIP`.

## 9. Verification

- targeted Radar v1.2 unit tests: 45 passed;
- Ruff lint: passed;
- Ruff format check: passed;
- mypy: passed for 37 source files;
- full pytest: passed (221 unit tests; 22 integration tests skipped without DB URL);
- PostgreSQL integration: 22 passed;
- Alembic autogenerate/schema check: no new upgrade operations;
- Docker Compose config: passed;
- application image build: passed;
- local LibreTranslate live validation: 10/10 completed with cache equality;
- live collection smoke: completed without delivery;
- manual URL smoke: completed twice with deduplication and no delivery.

Targeted coverage includes Reddit disabled/429 isolation, manual ingestion/dedup/private
URL rejection, local translation/fallback/cache/no OpenAI translation, verdict schema,
automatic versus manual `SKIP`, complete sentences/no ellipsis, absence of retired card
blocks, preserved feedback buttons, and the existing soft culture-balance behavior.

## 10. Implementation paths

- configuration/catalog: `.env.example`, `config/feeds.json`, `docker-compose.yml`;
- domain/schema/storage: `f117/domain.py`, `f117/storage/`, migration `0009`;
- fetch/translation/editorial/Telegram adapters: `f117/adapters/`;
- gating and output validation: `f117/pipeline/`;
- manual intake, digest, reports, runtime: `f117/services/`;
- documentation: `README.md`, this report;
- targeted unit and PostgreSQL integration tests: `tests/`.
