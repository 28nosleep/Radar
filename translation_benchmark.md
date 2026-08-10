# Radar v1.2.1 translation benchmark

Benchmark date: 2026-08-10. Target hardware: Apple M1, 8 GB RAM. All tested
translations ran locally; recurring API cost was $0. OpenAI was not used for
translation or corpus scoring. Production wiring was not changed.

## Method

The fixed 25-item corpus is stored in `benchmarks/translation_corpus.json`. It covers
AI announcements, model releases, robotics, GitHub, research, cyberculture, hardware,
corporate claims, headlines, factual paragraphs, entities, numbers, percentages, model
names, and URLs. Review is deterministic/manual and Radar-specific; BLEU/COMET was not
used because there is no single human reference and practical terminology errors such as
`forks` → `вилки` matter more here than n-gram similarity.

Latency is one sequential CPU translation per fragment after warm-up. p95 is the nearest
rank over 25 measurements. RAM is observed RSS/container memory, not a theoretical minimum.
Quality is the mean of accuracy, naturalness, terminology, and reliability. Performance is
the mean of speed and resource efficiency. Weighted Radar score = quality × 60% +
performance × 25% + deployment simplicity × 15%.

Model references checked before downloads: [LibreTranslate](https://github.com/LibreTranslate/LibreTranslate),
[Argos Translate](https://github.com/argosopentech/argos-translate),
[Helsinki OPUS-MT EN→RU](https://huggingface.co/Helsinki-NLP/opus-mt-en-ru),
[NLLB-200 distilled 600M](https://huggingface.co/facebook/nllb-200-distilled-600M), and
[M2M100 418M](https://huggingface.co/facebook/m2m100_418M).

## Aggregate scores

| Rank | Translator | Accuracy | Naturalness | Terminology | Reliability | Speed | Resource efficiency | Deployment | Radar score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Argos Translate direct, package 1.9 | 7.4 | 6.4 | 5.8 | 8.0 | 9.0 | 8.5 | 7.5 | **7.45** |
| 2 | Current LibreTranslate 1.9.6 + Radar token guard | 7.2 | 6.2 | 5.6 | 6.0 | 8.5 | 6.5 | 8.5 | **6.90** |
| 3 | Helsinki-NLP/opus-mt-en-ru | 5.8 | 5.2 | 4.0 | 6.5 | 5.5 | 7.5 | 6.5 | **5.83** |
| — | NLLB-200 distilled 600M | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | UNSUITABLE HERE |
| — | M2M100 418M | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED | UNSUITABLE HERE |

## Runtime and operations

| Translator | Median | Approx. p95 | RAM | Model/disk | Cached cold start | Docker/M1 assessment |
|---|---:|---:|---:|---:|---:|---|
| LibreTranslate | 254.27 ms | 396.05 ms | 1.48 GiB container | 374 MB Argos store (EN↔RU) | not restarted; production service remained up | Already deployed and healthy; simplest current operations |
| Argos direct | 201.22 ms | 278.41 ms | 348.65 MiB process | same 374 MB bilingual store | 421.07 ms first call | Best local balance; integration/health/cache work would be required |
| OPUS-MT | 479.19 ms | 831.62 ms | 526.02 MiB process | 307 MB PyTorch weights; 590 MB HF cache | 1831.66 ms cached load | Runs on M1 CPU; slower and materially worse Russian |
| NLLB-200 600M | — | — | expected to exceed safe headroom | 2.46 GB weights / 2.48 GB repo | — | NOT TESTED: 8 GB host and 3.826 GiB Docker limit; current translator already used ~1.48 GiB |
| M2M100 418M | — | — | expected to exceed safe headroom | 1.94 GB weights / 3.88 GB repo | — | NOT TESTED: same memory constraint; no result was imitated |

NLLB is additionally licensed CC-BY-NC-4.0, which is a deployment caveat. M2M100 is MIT;
OPUS is Apache-2.0. Every tested path has recurring API cost $0.

## Practical findings

1. **Argos direct** translated all 25 fragments and was fastest/lightest. It still produced
   literal software terminology (`pull request` → `запрос на вытягивание`) and changed
   entities (`Anthropic` → `Антропик`, `Claude` → `Клод`, `Figure AI` → `Фигурный ИИ`).
2. **Current LibreTranslate** preserved protected entities/numbers/URLs when restore succeeded,
   but token restore failed on 3/25 fragments and production-equivalent behavior fell back to English.
   It also rendered the critical GitHub case as `звезда 1, вилки 0`.
3. **OPUS-MT** had serious domain errors: `inference` → `нападение`, `Figure AI` →
   `На рисунке АИ`, and `fork the repository` → `винить хранилище`. It is not suitable for
   Radar technical copy despite moderate resource use.
4. No tested model was strong enough to treat the output as publication-quality Russian
   without editorial reasoning. The ranking concerns utility as local pre-translation only.

## Ranking and recommendations

1. **Argos Translate direct** — strongest overall and best reliability/resource balance.
   Recommendation: best candidate for a future controlled integration experiment, but only
   after an explicit user command and with Radar entity/terminology protection repaired.
2. **Current LibreTranslate** — operationally simplest and best entity guard when it works,
   but HTTP/container overhead and 12% English fallback lower reliability. Recommendation:
   keep current production provider until a separately authorized change.
3. **Helsinki OPUS-MT** — easy single-pair model and zero recurring cost, but Russian technical
   quality is too weak. Recommendation: do not adopt.

**BEST QUALITY:** Argos Translate direct (among actually tested candidates).

**BEST BALANCE:** Argos Translate direct.

**LIGHTEST:** Argos Translate direct by observed process RAM and latency.

**CURRENT LIBRETRANSLATE POSITION:** #2 of 3 tested candidates; #1 for current deployment simplicity.

## Per-example deterministic review

### Example 01

SOURCE:

OpenAI announced GPT-5 and GPT-5.4 for coding, reasoning, and long-running agent tasks.

LIBRETRANSLATE:

OpenAI анонсировал GPT-5 и GPT-5.4 для кодирования, рассуждения и долгосрочных задач агента.

ARGOS DIRECT:

OpenAI анонсировала GPT-5 и GPT-5.4 для кодирования, рассуждения и долгосрочных задач агента.

OPUS-MT:

OpenAI объявила GPT-5 и GPT-5.4 для целей кодирования, рассуждения и выполнения долгосрочных задач агента.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 02

SOURCE:

Anthropic says Claude can now coordinate multiple agents without human intervention.

LIBRETRANSLATE:

Anthropic says Claude can now coordinate multiple agents without human intervention.

ARGOS DIRECT:

Антропик говорит, что Клод теперь может координировать несколько агентов без вмешательства человека.

OPUS-MT:

Антропия говорит, что Клод теперь может координировать множество агентов без вмешательства человека.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 03

SOURCE:

NVIDIA reduced inference latency by 35% on the same GPU hardware.

LIBRETRANSLATE:

NVIDIA уменьшает задержку вывода на 35% на том же GPU оборудовании.

ARGOS DIRECT:

NVIDIA уменьшила задержку вывода на 35% на том же оборудовании GPU.

OPUS-MT:

NVIDIA сократила запоздалость на 35% на одном и том же аппаратуре GPU.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 04

SOURCE:

Figure AI claims its humanoid completed a full 8-hour shift at a BMW factory.

LIBRETRANSLATE:

Figure AI утверждает, что его гуманоид завершил полную 8-часовую смену на заводе BMW.

ARGOS DIRECT:

Фигурный ИИ утверждает, что его гуманоид завершил полную 8-часовую смену на заводе BMW.

OPUS-MT:

На рисунке АИ утверждается, что его гуманоид закончил полную 8-часовую смену на фабрике БМВ.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 05

SOURCE:

Boston Dynamics demonstrated Atlas handling parts in an industrial workspace.

LIBRETRANSLATE:

Boston Dynamics продемонстрировал Atlas обработку деталей в промышленном рабочем пространстве.

ARGOS DIRECT:

Boston Dynamics продемонстрировала Atlas в промышленной рабочей зоне.

OPUS-MT:

Boston Dynamics продемонстрировал, что Атлас обрабатывает детали в промышленном рабочем пространстве.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 06

SOURCE:

The GitHub repository has 1 star, 0 forks, and no recorded growth.

LIBRETRANSLATE:

В хранилище GitHub есть звезда 1, вилки 0 и нет зарегистрированного роста.

ARGOS DIRECT:

Репозиторий GitHub имеет 1 звезду, 0 вилок и не имеет зарегистрированного роста.

OPUS-MT:

Хранилище GitHub имеет 1 звезду, 0 вилок, и никакого зарегистрированного роста.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 07

SOURCE:

After 24 hours, the project reached 1,240 stars and 87 forks on GitHub.

LIBRETRANSLATE:

After 24 hours, the project reached 1,240 stars and 87 forks on GitHub.

ARGOS DIRECT:

Через 24 часа проект достиг 1240 звезд и 87 форков на GitHub.

OPUS-MT:

Через 24 часа проект достиг 1240 звёзд и 87 вилок на Гитхубе.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 08

SOURCE:

The benchmark reports a 4.7% gain, but the authors did not publish the test set.

LIBRETRANSLATE:

В бенчмарке сообщается о приросте 4.7%, но авторы не опубликовали тестовый набор.

ARGOS DIRECT:

В бенчмарке сообщается о росте на 4,7%, но авторы не опубликовали тестовый набор.

OPUS-MT:

Согласно контрольному показателю, прирост составил 4,7 процента, однако авторы не опубликовали этот набор тестов.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 09

SOURCE:

Inference runs locally on a Mac M1 with 8 GB of RAM.

LIBRETRANSLATE:

Вывод выполняется локально на Mac M1 с 8 GB оперативной памяти.

ARGOS DIRECT:

Inference работает локально на Mac M1 с 8 ГБ оперативной памяти.

OPUS-MT:

Нападение происходит локально на Mac M1 с 8 GB RAM.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 10

SOURCE:

The agent opened a pull request, but it did not run the security tests.

LIBRETRANSLATE:

Агент открыл запрос на вытягивание, но он не проводил тесты безопасности.

ARGOS DIRECT:

Агент открыл запрос на вытягивание, но он не проводил тесты безопасности.

OPUS-MT:

Агент открыл запрос, но он не провел тесты безопасности.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 11

SOURCE:

Researchers released an open-source model under the Apache-2.0 license.

LIBRETRANSLATE:

Исследователи выпустили модель с открытым исходным кодом под лицензией Apache-2.0.

ARGOS DIRECT:

Исследователи выпустили модель с открытым исходным кодом под лицензией Apache-2.0.

OPUS-MT:

Исследователи выпустили модель с открытым исходным кодом по лицензии Apache-2.0.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 12

SOURCE:

Fine-tuning improved accuracy from 81.2% to 84.9% on 500 examples.

LIBRETRANSLATE:

Точная настройка улучшила точность с 81.2% до 84.9% на примерах 500.

ARGOS DIRECT:

Точная настройка улучшила точность с 81,2% до 84,9% на 500 примерах.

OPUS-MT:

По 500 примерам точность уточнений повысилась с 81,2% до 84,9%.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 13

SOURCE:

The alignment study found that larger models followed harmful instructions less often.

LIBRETRANSLATE:

Исследование выравнивания показало, что более крупные модели реже следовали вредным инструкциям.

ARGOS DIRECT:

Исследование выравнивания показало, что более крупные модели реже следовали вредным инструкциям.

OPUS-MT:

Исследование по согласованию показало, что более крупные модели реже следуют вредным инструкциям.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 14

SOURCE:

Synthetic media is making it harder to verify footage from breaking news events.

LIBRETRANSLATE:

Синтетические средства массовой информации затрудняют проверку отснятого материала с новостных событий.

ARGOS DIRECT:

Синтетические средства массовой информации затрудняют проверку отснятого материала с новостных событий.

OPUS-MT:

Синтетические средства массовой информации затрудняют проверку видеозаписей о чрезвычайных событиях.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 15

SOURCE:

A cyberpunk short film generated with AI went viral after its director disclosed the workflow.

LIBRETRANSLATE:

Киберпанк короткометражный фильм, созданный с помощью ИИ, стал вирусным после того, как его режиссер раскрыл рабочий процесс.

ARGOS DIRECT:

Киберпанк короткометражный фильм, созданный с помощью ИИ, стал вирусным после того, как его режиссер раскрыл рабочий процесс.

OPUS-MT:

После того, как его режиссёр раскрыл рабочий процесс, короткометражный фильм, спровоцированный АИ, стал вирусным.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 16

SOURCE:

Read the API notes at https://example.com/docs/v2 before upgrading.

LIBRETRANSLATE:

Прочтите заметки API в https://example.com/docs/v2 перед обновлением.

ARGOS DIRECT:

Прочитайте примечания API на https://example.com/docs/v2 перед обновлением.

OPUS-MT:

Перед обновлением прочитайте примечания АПИ на сайте https://example.com/docs/v2.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 17

SOURCE:

Download model-v3.1 from https://github.com/example/model/releases/tag/v3.1.

LIBRETRANSLATE:

Загрузить модель-v3.1 из https://github.com/example/model/releases/tag/v3.1.

ARGOS DIRECT:

Скачать модель-v3.1 с https://github.com/example/model/releases/tag/v3.1.

OPUS-MT:

Загрузить модель-v3.1 с https://github.com/example/model/releases/tag/v3.1.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 18

SOURCE:

The company calls it a breakthrough, but provides no benchmark, customers, or independent evidence.

LIBRETRANSLATE:

Компания называет это прорывом, но не предоставляет никаких ориентиров, клиентов или независимых доказательств.

ARGOS DIRECT:

Компания называет это прорывом, но не предоставляет никаких ориентиров, клиентов или независимых доказательств.

OPUS-MT:

Компания называет это прорывом, но не предоставляет ни ориентиров, ни клиентов, ни независимых доказательств.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 19

SOURCE:

A new robot learned the task from 12 demonstrations and succeeded in 73 of 100 trials.

LIBRETRANSLATE:

Новый робот выучил задачу из демонстраций 12 и преуспел в испытаниях 73 100.

ARGOS DIRECT:

Новый робот выучил задание из 12 демонстраций и преуспел в 73 из 100 испытаний.

OPUS-MT:

Новый робот узнал об этой задаче из 12 демонстраций и добился успеха в 73 из 100 испытаний.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 20

SOURCE:

The arXiv paper evaluates manipulation in simulation, not on physical robots.

LIBRETRANSLATE:

The arXiv paper evaluates manipulation in simulation, not on physical robots.

ARGOS DIRECT:

Статья arXiv оценивает манипуляции в моделировании, а не на физических роботах.

OPUS-MT:

Газета ArXiv оценивает манипулирование в симуляции, а не на физических роботах.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 21

SOURCE:

This hardware accelerator delivers 420 TOPS at 75 W, according to the vendor.

LIBRETRANSLATE:

Этот аппаратный ускоритель поставляет TOPS 420 на 75 W, по словам поставщика.

ARGOS DIRECT:

По словам поставщика, этот аппаратный ускоритель обеспечивает 420 TOPS при 75 Вт.

OPUS-MT:

Этот акселератор доставляет 420 ТОПС при 75 Вт, по словам продавца.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 22

SOURCE:

The headline says AI replaced the whole team; the article describes one automated spreadsheet.

LIBRETRANSLATE:

В заголовке говорится, что ИИ заменил всю команду; в статье описывается одна автоматизированная таблица.

ARGOS DIRECT:

В заголовке говорится, что ИИ заменил всю команду; в статье описывается одна автоматизированная таблица.

OPUS-MT:

В заголовке говорится, что AI заменила всю команду; в статье описывается одна автоматизированная таблица.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 23

SOURCE:

GitHub users can fork the repository, star it, and run inference on their own hardware.

LIBRETRANSLATE:

GitHub пользователи могут раскрутить репозиторий, запустить его и сделать вывод на своем собственном оборудовании.

ARGOS DIRECT:

Пользователи GitHub могут разветвить репозиторий, запустить его и сделать вывод на своем собственном оборудовании.

OPUS-MT:

Пользователи GitHub могут винить хранилище, звездировать его и делать выводы на собственном оборудовании.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 24

SOURCE:

Claude 4.5 and GPT-5 were tested on the same agent benchmark at 128k context.

LIBRETRANSLATE:

Claude 4.5 и GPT-5 были протестированы на одном и том же бенчмарке агентов в контексте 128k.

ARGOS DIRECT:

Claude 4.5 и GPT-5 были протестированы на одном и том же бенчмарке агентов в контексте 128k.

OPUS-MT:

Клод 4.5 и GPT-5 были испытаны на основе одного и того же контрольного показателя по агенту при 128k контексте.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

### Example 25

SOURCE:

OpenAI, Anthropic, NVIDIA, Figure AI, and Boston Dynamics did not comment by publication time.

LIBRETRANSLATE:

OpenAI, Anthropic, NVIDIA, Figure AI и Boston Dynamics не комментировали время публикации.

ARGOS DIRECT:

OpenAI, Anthropic, NVIDIA, Figure AI и Boston Dynamics не прокомментировали время публикации.

OPUS-MT:

OpenAI, Anthropic, NVIDIA, Chart AI и Boston Dynamics не комментировали по времени публикации.

NLLB-200 DISTILLED 600M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

M2M100 418M:

NOT TESTED / UNSUITABLE IN THIS ENVIRONMENT (resource limit; no synthetic result).

## Final status

Temporary OPUS venv/model cache and benchmark files inside the translator container were
removed after measurement. The existing production LibreTranslate service remained
healthy and running.

**PRODUCTION TRANSLATOR CHANGED: NO**
