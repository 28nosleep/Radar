# Radar v1.2.1 validation report

Validation date: 2026-08-10. This is a narrow quality/research patch. No new source,
scheduler change, Reddit change, deterministic threshold change, scoring system, culture
balance change, migration, production translator switch, or Telegram delivery was made.

## 1. Result

The automatic AI-verdict gate is now centralized and strict: only `STRONG` and
`INTERESTING` may be delivered automatically. Manual Share → Radar still renders all five
verdicts honestly. An empty automatic digest is a valid result.

In the 80-material historical sample, deterministic replay produced 7 finalists. Current
editorial reasoning classified them as 3 `INTERESTING`, 3 `WEAK`, and 1 `SKIP`. The old
policy would deliver 6; v1.2.1 would deliver 3. The patch therefore removed all 3 WEAK
cards and reduced automatic output by 50% in this small replay.

## 2. WEAK delivery policy: before and after

| AI verdict | Automatic before | Automatic after | Manual before/after |
|---|---:|---:|---:|
| STRONG | DELIVER | DELIVER | SHOW |
| INTERESTING | DELIVER | DELIVER | SHOW |
| WEAK | DELIVER | **DO NOT DELIVER** | SHOW |
| HYPE | Culture exception only | **DO NOT DELIVER** | SHOW |
| SKIP | DO NOT DELIVER | DO NOT DELIVER | SHOW |

`is_automatic_ai_verdict_deliverable()` owns the automatic verdict set. No verdict was
rewritten and no deterministic threshold was changed to compensate.

## 3. Historical replay methodology

The PostgreSQL database contained 1,819 materials, but its immutable observation history
covered only 1.07 days (2026-08-09T11:29:08.638214+00:00 → 2026-08-10T13:03:36.560872+00:00), not
the requested 7–14 days. The replay therefore used the available 80-material stratified
sample and does not claim multi-day statistical power.

Sample strata: 22 official/primary-news candidates, 12 Hacker News, 8 YouTube, 10 GitHub,
10 arXiv, 12 culture-media, and 6 legacy Reddit records. GitHub/arXiv were bounded at 20/80.

| Deterministic stage | Count |
|---|---:|
| COLLECTED | 80 |
| FUTURE OBSERVATION rejected | 0 |
| FRESHNESS rejected | 26 |
| DEDUP rejected | 0 |
| EDITORIAL GATE rejected | 47 |
| RANKING eligible | 7 |
| FINALIST | 7 |

Finalist source mix: Hacker News 3; OpenAI News 1; 404 Media 1; The Verge 1; legacy
Reddit 1. Finalist category assignments: AI 5, LLM 1, open source 1, robotics 1; cards can
carry more than one category.

Stage B used exactly 7 current production editorial calls, below the 20-card cap. The 44
saved enrichments used a legacy incompatible schema (`why_important`, no `ai_verdict`) and
were not reused. OpenAI was used only for existing editorial reasoning after local
LibreTranslate; it was not used as a translation candidate.

## 4. No future leakage and read-only guarantees

- `as_of` was fixed at `2026-08-10T13:03:36.722644+00:00`.
- Immutable database `created_at` was used as the observation time because provider refresh
  can overwrite `collected_at`.
- Metric histories were filtered to `captured_at <= as_of`; 50/80 materials had no usable
  historical metrics and were treated as UNKNOWN/empty, not filled with today's values.
- Historical independent mentions were conservatively fixed at 1 and temporal qualitative
  signals were omitted where their historical state could not be reconstructed.
- Persisted delivery, selection, retry, enrichment, and feedback state were removed from
  decision inputs. No future growth, cross-source mention, feedback, or delivered flag was used.
- Replay code has no repository/notifier/feedback input and runs in memory. SQL inspection ran
  under `SET TRANSACTION READ ONLY`.
- Before/after database checks remained `delivered=44`, `enriched=44`, `feedback=14`.
- Telegram was never constructed or called.

## 5. Historical finalists

| Historical time | Title | Source | Category | Fit | Importance | Delivery | Verdict | Before | After | Reason |
|---|---|---|---|---:|---:|---:|---|---:|---:|---|
| 2026-08-10T13:03:36.560872+00:00 | Meta releases new on-device optimized open source model | Reddit: r/singularity | open_source | 74.00 | 41.68 | 62.69 | SKIP | NO | NO | Одиночный Reddit-пост без первоисточника, модели, деталей и тестов. |
| 2026-08-10T13:03:35.198987+00:00 | Meta's new open-weight model targets local agentic AI | Hacker News | ai | 66.00 | 50.34 | 60.52 | WEAK | YES | NO | Нет исходного анонса, характеристик, независимых тестов или adoption. |
| 2026-08-10T13:03:35.457266+00:00 | The Roboguard Revolution is Short-Circuiting | 404 Media | robotics | 66.00 | 44.98 | 58.64 | INTERESTING | YES | YES | Полезный контрсигнал о проблемах внедрения охранных роботов. |
| 2026-08-10T11:47:34.667778+00:00 | Ford’s new AI assistant can check your fuel levels and tire pressure | The Verge: Entertainment & Culture | ai | 66.00 | 44.22 | 58.38 | WEAK | YES | NO | Обычный интерфейс к существующей телеметрии без данных о качестве и rollout. |
| 2026-08-10T07:03:33.888414+00:00 | Docker Sandboxes – Disposable, isolated sandboxes for AI agents | Hacker News | ai | 58.00 | 55.13 | 57.00 | INTERESTING | YES | YES | Практичная изоляция agent workloads; HN подтверждает актуальность, не качество. |
| 2026-08-10T13:03:35.015831+00:00 | Over 181,000 AI meeting recordings left wide open in note taking app | Hacker News | ai | 58.00 | 49.97 | 55.19 | WEAK | YES | NO | Только заголовок без отчёта, продукта и независимой проверки масштаба. |
| 2026-08-09T11:29:08.638214+00:00 | Responding to the next frontier of critical cyber capabilities | OpenAI News | ai, llm | 71.00 | 33.22 | 57.78 | INTERESTING | YES | YES | Заметная оценка киберрисков OpenAI, но self-report без методик и внешней проверки. |

Top retained examples were Docker Sandboxes, the 404 Media account of failing
roboguard deployments, and OpenAI's preliminary cyber-capability evaluation. The three
removed WEAK cards were Meta local-agent claims without a primary source, Ford's routine
assistant feature, and a meeting-recording exposure represented only by a headline.

## 6. Feedback comparison after decisions

Feedback was joined only after all replay decisions were frozen. None of the 7 finalists
had a known `👍 Полезно`, `👎 Мимо`, or `⭐ В пост` label, although the database contained
14 feedback rows outside this finalist set.

| Metric | Old policy | New policy |
|---|---:|---:|
| Would deliver | 6 | 3 |
| Known 👍 | 0 | 0 retained |
| Known 👎 | 0 | 0 removed |
| Known ⭐ | 0 | 0 retained |
| Feedback UNKNOWN | 6 | 3 |

The fix removes 3 WEAK cards, but the sample cannot establish how many were user-labelled
bad or how many genuinely useful/saved cards were lost. Known useful/saved lost = 0, but
all three removed WEAK cards are unlabeled, so that is not evidence of zero real loss.

## 7. Product metrics

- cards/day equivalent: 5.63 before, 2.82 after;
- STRONG: 0; INTERESTING: 3;
  WEAK: 3; HYPE: 0; SKIP: 1;
- automatic delivery: 6 before, 3 after; reduction: 50%;
- known useful retained: 0/0; known saved retained: 0/0; known misses removed: 0/0.

Radar is demonstrably quieter in this replay. The retained verdict mix is stronger by
editorial label, but the available feedback is insufficient to claim proven user utility.

## 8. Translation benchmark corpus

The full per-example SOURCE/Libre/Argos/OPUS/NOT TESTED output and deterministic manual
review are in `translation_benchmark.md`; the machine-readable fixed source corpus is in
`benchmarks/translation_corpus.json`. The 25 sources are:

1. OpenAI announced GPT-5 and GPT-5.4 for coding, reasoning, and long-running agent tasks.
2. Anthropic says Claude can now coordinate multiple agents without human intervention.
3. NVIDIA reduced inference latency by 35% on the same GPU hardware.
4. Figure AI claims its humanoid completed a full 8-hour shift at a BMW factory.
5. Boston Dynamics demonstrated Atlas handling parts in an industrial workspace.
6. The GitHub repository has 1 star, 0 forks, and no recorded growth.
7. After 24 hours, the project reached 1,240 stars and 87 forks on GitHub.
8. The benchmark reports a 4.7% gain, but the authors did not publish the test set.
9. Inference runs locally on a Mac M1 with 8 GB of RAM.
10. The agent opened a pull request, but it did not run the security tests.
11. Researchers released an open-source model under the Apache-2.0 license.
12. Fine-tuning improved accuracy from 81.2% to 84.9% on 500 examples.
13. The alignment study found that larger models followed harmful instructions less often.
14. Synthetic media is making it harder to verify footage from breaking news events.
15. A cyberpunk short film generated with AI went viral after its director disclosed the workflow.
16. Read the API notes at https://example.com/docs/v2 before upgrading.
17. Download model-v3.1 from https://github.com/example/model/releases/tag/v3.1.
18. The company calls it a breakthrough, but provides no benchmark, customers, or independent evidence.
19. A new robot learned the task from 12 demonstrations and succeeded in 73 of 100 trials.
20. The arXiv paper evaluates manipulation in simulation, not on physical robots.
21. This hardware accelerator delivers 420 TOPS at 75 W, according to the vendor.
22. The headline says AI replaced the whole team; the article describes one automated spreadsheet.
23. GitHub users can fork the repository, star it, and run inference on their own hardware.
24. Claude 4.5 and GPT-5 were tested on the same agent benchmark at 128k context.
25. OpenAI, Anthropic, NVIDIA, Figure AI, and Boston Dynamics did not comment by publication time.

## 9. Translator results and ranking

| Rank | Translator | Quality summary | Median / p95 | RAM/model | Radar score | Recommendation |
|---:|---|---|---:|---|---:|---|
| 1 | Argos direct 1.9 | Best tested practical quality; entity/technical literals remain | 201/278 ms | 349 MiB; shared 374 MB EN↔RU store | 7.45 | Future controlled candidate only |
| 2 | Current LibreTranslate 1.9.6 | Strong entity guard, but 3/25 English fallbacks and `вилки` | 254/396 ms | 1.48 GiB container; 374 MB store | 6.90 | Keep until separate command |
| 3 | Helsinki OPUS-MT | Severe domain errors including inference→нападение | 479/832 ms | 526 MiB; 307 MB weights | 5.83 | Do not adopt |

NLLB-200 distilled 600M and M2M100 418M were **NOT TESTED / UNSUITABLE** here: verified
weights are 2.46 GB and 1.94 GB respectively, while the host has 8 GB RAM and Docker is
limited to 3.826 GiB with the current translator already using about 1.48 GiB. No output
was imitated. NLLB's CC-BY-NC-4.0 license is an additional deployment caveat. All tested
candidates have recurring API cost $0.

**BEST QUALITY:** Argos Translate direct (among tested candidates).

**BEST BALANCE:** Argos Translate direct.

**LIGHTEST:** Argos Translate direct by observed process RAM/latency.

**CURRENT LIBRETRANSLATE POSITION:** #2 of 3 tested; #1 for current deployment simplicity.

**PRODUCTION TRANSLATOR CHANGED: NO**

## 10. Tests and validation

- targeted policy/manual/empty-digest/replay/culture/Telegram suite: 58 passed;
- full pytest: 234 passed, 22 PostgreSQL tests skipped without DB URL;
- PostgreSQL integration: 22 passed;
- Ruff lint: passed; Ruff format check: 68 files formatted;
- mypy strict: passed for 39 source files;
- Alembic/schema check: no new upgrade operations; no migration created;
- `docker compose config --quiet`: passed;
- `git diff --check`: passed;
- translator container remained healthy after temporary model cleanup;
- replay mutation check: delivered/enriched/feedback counts unchanged; Telegram sends: 0.

Acceptance coverage includes automatic delivery for STRONG/INTERESTING only; no automatic
WEAK/HYPE/SKIP; manual visibility for all five verdicts; empty digest; unchanged feedback
buttons and culture balance; no replay Telegram/state mutation; historical freshness cutoff;
future snapshots ignored; and feedback excluded from decisions.

## 11. Changed files

- `f117/services/delivery_policy.py` — centralized verdict policy;
- `f117/services/digest.py` — automatic gate delegates to central policy;
- `f117/services/historical_replay.py` — pure read-only/as-of replay;
- `tests/unit/test_editorial_output.py` — five automatic and five manual verdict cases;
- `tests/unit/test_digest_service.py` — explicit empty-digest acceptance;
- `tests/unit/test_historical_replay.py` — as-of, future metrics, no mutation/inputs;
- `tests/integration/test_reliability_batch_a.py` — explicit deliverable fixture verdict;
- `benchmarks/translation_corpus.json` — fixed 25-item corpus;
- `translation_benchmark.md` — full translations, scores, resources, ranking;
- `radar_v1_2_1_validation.md` — this report.

Unrelated pre-existing workspace changes in `.env.example`, `config/feeds.json`, CLI/runtime,
and their tests were not included in the implementation commit.

## 12. Commit

Implementation commit: `88b17a175ce193e88443c80b2fd28badee0bd716`
(`fix: tighten Radar automatic delivery quality gate`).

The validation report is committed separately so it can cite the immutable implementation
hash without a self-referential commit-hash loop.
