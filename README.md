# Radar — Intelligence Engine

Персональная разведывательная лента для одного владельца. Текущий Milestone M4 —
полностью проходящий вертикальный срез:

`RSS / Hacker News / arXiv / GitHub / Reddit / YouTube → нормализация → классификация → дедупликация → rule-based TOP-10 → OpenAI → личный Telegram`

Проект ничего не публикует в канал. Telegram принимает только положительный numeric
`chat_id`, то есть личный чат; отрицательные ID групп и каналов отклоняются.

## Упрощённый план

Каждый этап заканчивается запускаемой системой, тестами, миграцией и документацией.

1. **M1 — RSS vertical slice (завершён).** Фиксированный JSON-каталог RSS, общая
   модель, консервативные дубли, keyword-категории, объяснимый score, максимум TOP-10
   запросов к OpenAI и Telegram/dry-run.
2. **M2 — Hacker News и arXiv (завершён).** Два бесплатных коллектора подключаются к
   той же модели; весь downstream-пайплайн остаётся неизменным.
3. **M3 — GitHub, Reddit и YouTube (завершён).** Добавляются API-коллекторы и снимки
   метрик. X не входит в этап.
4. **M4 — Discovery (текущий).** Дельты stars/upvotes/comments/views, скорость роста,
   cross-source сигнал и скрытые находки — без ML и без GPT.
5. **M5 — персональная настройка.** Telegram feedback, ручная корректировка весов и
   каталога, компактные CLI-статусы. Без автоматического обучения и тяжёлой панели.

## Архитектура M4

Это модульный монолит и один процесс-планировщик. Очередь, Redis, Celery/ARQ,
FastAPI и отдельные сервисы не нужны. Запуски не пересекаются; источники внутри
одного запуска скачиваются асинхронно с настраиваемым лимитом.

Главный контракт данных:

`CollectedItem → NormalizedItem → StoredMaterial → RankedMaterial → EditorialCard`

- `adapters/` — RSS, Hacker News, arXiv, OpenAI Responses API и Telegram Bot API;
- `pipeline/` — чистые детерминированные алгоритмы;
- `storage/` — SQLAlchemy/PostgreSQL и история запусков/доставок;
- `services/` — один application workflow;
- `cli/` — разовый запуск, scheduler, проверка настроек и статус.

OpenAI получает только уже отсортированный TOP-N (`10` по умолчанию). Результат
сохраняется до Telegram. Уже обогащённые, но недоставленные карточки образуют отдельную
FIFO-очередь повторной доставки и всегда обрабатываются раньше новых материалов; это не
тратит GPT повторно. При сбое OpenAI реальная доставка такой карточки откладывается до
следующего запуска, а dry-run показывает локальный fallback.

## Быстрый запуск

Нужны Docker и Docker Compose.

```bash
cp .env.example .env
docker compose build
docker compose up -d postgres
docker compose run --rm app alembic upgrade head
docker compose run --rm app radar validate-config
docker compose run --rm app radar run-once
```

По умолчанию включён безопасный `dry-run`: OpenAI и Telegram не вызываются, готовая
подборка печатается в stdout и материалы не считаются доставленными.

Для реальной личной доставки в `.env` нужны:

```dotenv
F117_DRY_RUN=false
F117_OPENAI_ENABLED=true
F117_OPENAI_API_KEY=...
F117_TELEGRAM_ENABLED=true
F117_TELEGRAM_BOT_TOKEN=...
F117_TELEGRAM_CHAT_ID=123456789
```

После проверки запустите постоянный процесс:

```bash
docker compose up -d
docker compose logs -f app
```

Интервал по умолчанию — 180 минут. Все несущественные числа, включая TOP-N,
dedup threshold, concurrency и веса score, вынесены в `.env.example`.

## Каталог источников

Источники находятся в `config/feeds.json`. Для каждого задаются стабильный `key`,
название, URL, репутация `0..1`, флаг активности и категории по умолчанию.
Удалённый из каталога источник выключается в базе, но его история сохраняется.

RSS/Atom использует ETag/Last-Modified. Hacker News работает через официальный Firebase
API; по умолчанию собирается `top` (в конфиге можно выбрать `new` или `best`) и 30
историй с points/comments. arXiv использует публичный Atom API и по умолчанию читает
`cs.AI`, `cs.LG`, `cs.CL`, `cs.CV`, `cs.RO`, `eess.SY`. Ошибка одного источника не
останавливает остальные.

GitHub ищет настроенные запросы по новым репозиториям, сохраняет stars, forks, язык,
topics и последний release. Reddit собирает разрешённые сабреддиты с score/comments и
текстом поста; низкий вес задан в каталоге для `r/OpenAI`, `r/ControlProblem`,
`r/technology` и `r/ChatGPT`. YouTube использует Data API для настроенных каналов и
поисковых запросов. Он включается только после добавления `F117_YOUTUBE_API_KEY` и
переключения источника в `config/feeds.json` на `enabled: true`.

## История метрик

Для GitHub, Reddit и YouTube каждый повторно увиденный материал получает снимок в
`metric_snapshots`. Между двумя снимками рассчитываются `growth_absolute`,
`growth_percent`, `growth_per_hour` и окно измерения. Это обычный ranking-сигнал, а не
Discovery Engine: аномалии и отдельные alerts остаются задачей M4. В editorial Telegram
строка `Набирает: +…% за … ч` появляется только при наличии двух снимков.

В `.env` можно добавить необязательный `F117_GITHUB_API_TOKEN` (выше rate limit) и
`F117_YOUTUBE_API_KEY`. Ключи никогда не коммитятся.

## Discovery Engine

M4 не заменяет importance score: основной ranking по-прежнему отвечает на вопрос
«насколько материал важен», а отдельный `discovery_score` — «насколько интересно его
появление или рост прямо сейчас». Он использует только имеющиеся snapshots: существенный
absolute growth, percent/per-hour growth, acceleration при трёх снимках, freshness,
novelty и число независимых источников. Процентный рост от маленькой базы подавляется
настраиваемыми `F117_DISCOVERY_MIN_BASELINE` и `F117_DISCOVERY_MIN_GROWTH_ABSOLUTE`.

Discovery лишь ограниченно усиливает выбор кандидатов через
`F117_DISCOVERY_SELECTION_BOOST`; он не вытесняет обычный importance ranking. Ранние
материалы с сильным ростом и умеренной абсолютной популярностью попадают в «Скрытые
находки». В Telegram показываются только подтверждённые сигналы роста или независимых
упоминаний; формулы и discovery score доступны лишь в debug-режиме.

Если Reddit OAuth credentials не заданы, все Reddit-источники штатно пропускаются с
информационным логом. Это не ошибка run и не блокирует RSS, HN, arXiv, GitHub или YouTube.

## Telegram

По умолчанию `F117_TELEGRAM_FORMAT=editorial`: каждая карточка содержит рубрику,
русский заголовок, краткий пересказ, строку «Почему это важно», источник, теги и ссылку.
Служебные score и formula breakdown не попадают в сообщение. Для диагностики можно
установить `F117_TELEGRAM_FORMAT=debug`; тогда они добавляются в конец карточки.

## Дедупликация и score

Дедупликация сначала проверяет source/external ID и канонический URL, затем точный
нормализованный контент/заголовок. Нечёткое объединение требует не менее шести слов,
сходства `0.92` и окна в три дня. При сомнении публикации остаются разными.

Score `0..100` объясним: свежесть, репутация, независимые упоминания, популярность,
скорость роста, новизна, соответствие темам и необычность. В RSS часть метрик обычно
пуста; полноценные popularity/growth появятся у API-коллекторов M3.

## Локальная проверка

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/mypy f117
.venv/bin/pytest
```

Миграции применяются командой `.venv/bin/alembic upgrade head`, а состояние базы
показывает `radar status`.

Имя продукта и основная CLI-команда — `radar`. Внутренний Python-пакет и префикс
переменных `F117_` сохранены для совместимости M1; алиас `f117` продолжает работать,
но в документации и Docker используется `radar`.
