# Acceptance review after live mode

Дата проверки: 2026-07-11.

Цель проверки: строго сверить Scout Pilot с исходным интервью-заданием после подключения `scout-pilot run --live` к обычному runtime loop. Формат статусов: `passes`, `partial`, `gap`.

## Итог

Общий статус: **passes with documented limitations**.

Проект можно защищать на интервью как честный Junior+/Middle Python AI developer проект: есть видимый браузер, persistent profile, текстовый CLI input, live runtime, semantic observations, Tool Runtime, context budgeting, memory/reflection, Security Policy и безопасные report/replay.

Критических gaps, которые ломают исходное задание, в этом проходе не найдено. Два ограничения нужно проговаривать прямо:

- live OpenAI/Anthropic smoke не подтвержден на этой машине, потому что локальный `.env` отсутствует;
- live HH.ru smoke в этой проверке не выполнялся, потому что он ручной, зависит от сети, аккаунта, CAPTCHA, региона и текущей верстки сайта.

## Проверки, выполненные в этом проходе

| Команда | Результат |
|---|---|
| `python -m pip install -e ".[dev]"` | `exit 0`; editable install собран и установлен как `scout-pilot 0.1.0`. |
| `python -m ruff check .` | `exit 0`; `All checks passed!`. |
| `python -m pytest -q` | `exit 0`; `179 passed in 27.15s`. |
| `scout-pilot doctor` | `exit 0`; критических блокеров нет, Chromium headless smoke прошел, `.env` отсутствует как предупреждение. |
| `scout-pilot status` | `exit 0`; CLI видит основные слои, live runtime, demo/reporting commands. |
| `scout-pilot run "Проверить локальный dry-run" --dry-run` | `exit 0`; браузер, LLM и отправка данных не выполнялись, report/replay созданы в ignored `reports/tmp`. |
| `scout-pilot profile-info` | `exit 0`; `.browser-profiles/default` существует и игнорируется Git. |
| `scout-pilot browser-smoke --headed --hold-seconds 0.1` | `exit 0`; видимый браузер запущен и закрыт через Browser Engine. |
| `scout-pilot run "Проверь локальный сайт и подготовь краткое наблюдение." --live --provider mock --start-url http://127.0.0.1:8766/index.html --headless --dashboard off --max-iterations 3 --report-path reports/tmp/acceptance-run-report.json --replay-path reports/tmp/acceptance-run-replay.json` | `exit 0`; обычный `run --live` открыл локальный URL, выполнил `browser.navigate`, `browser.observe`, записал report/replay и закрыл браузер. |
| `scout-pilot live-local-demo --headless --slow-mo-ms 0 --dashboard off --max-iterations 8 --report-path reports/tmp/acceptance-live-local-report.json --replay-path reports/tmp/acceptance-live-local-replay.json` | `exit 0`; агент прошел multi-step runtime flow, прочитал 3 detail-страницы, сделал 1 ambiguity check и остановился перед `Apply`. |
| `scout-pilot replay-summary reports/tmp/acceptance-live-local-replay.json` | `exit 0`; replay summary показал 5 страниц, 11 наблюдений, 7 tool calls, 2 security pauses, context `1869 -> 663` токенов. |
| `scout-pilot provider-smoke --provider openai` | `exit 1`; ожидаемо нет `OPENAI_API_KEY` в локальном `.env`. Это не ломает mock/demo, но live OpenAI smoke не подтвержден в этом окружении. |
| `rg -n "from playwright\|import playwright" src\scout_pilot \| rg -v "src[/\\]scout_pilot[/\\]browser"` | `exit 1`; прямых Playwright imports вне Browser Engine не найдено. |
| `rg -n "from openai\|import openai\|from anthropic\|import anthropic" src\scout_pilot \| rg -v "src[/\\]scout_pilot[/\\]llm"` | `exit 1`; provider SDK imports вне LLM layer не найдены. |
| `rg -n "hh\.ru\|data-qa\|/vacancy" src` | `exit 1`; hardcoded HH.ru selectors/routes в runtime/source не найдены. |
| `rg -n "page\.content\|inner_html\|outerHTML\|raw_html" src\scout_pilot \| rg -v "reporting[/\\]runtime_report.py\|reporting[/\\]replay_summary.py"` | `exit 1`; complete/raw HTML access в model-facing runtime source не найден. |

## Матрица требований

| Требование | Статус | Доказательства | Ограничение |
|---|---|---|---|
| Visible browser | passes | `scout-pilot browser-smoke --headed --hold-seconds 0.1`; `src/scout_pilot/browser/playwright_engine.py`; `docs/video_script.md`. | В автоматических тестах чаще используется headless, чтобы не зависеть от GUI. |
| Persistent session | passes | `scout-pilot profile-info`; `.browser-profiles/default` существует и ignored; `BrowserEngineConfig.user_data_dir`; `.gitignore`. | Логин не автоматизируется, профиль локальный и не экспортируется в repo. |
| Text task input from terminal | passes | `scout-pilot run "..." --dry-run`; `scout-pilot run "..." --live ...`; `src/scout_pilot/cli/main.py`. | `run` по умолчанию dry-run, live требует явный `--live`. |
| Live autonomous browser execution | passes | `run --live --provider mock --start-url ...` открыл страницу и выполнил observe; `live-local-demo` прошел click/resolve/navigate/security pause через runtime. | Полное качество на реальных сайтах зависит от провайдера, модели и страницы. |
| OpenAI/Anthropic provider support | partial | `src/scout_pilot/llm/openai_provider.py`, `anthropic_provider.py`, `tool_adapters.py`; provider tests в `tests/test_llm_providers.py`; `provider-smoke` команда есть. | `scout-pilot provider-smoke --provider openai` в этом проходе завершился `exit 1`, потому что `.env` и `OPENAI_API_KEY` отсутствуют. Live provider smoke нужно выполнить вручную с локальным ключом. |
| Playwright automation | passes | Browser smoke прошел; direct import scan показывает Playwright только в `scout_pilot.browser`. | Browser Engine использует Playwright internals, но публичные APIs не возвращают `Page`, `Browser`, `Context` или raw DOM handles. |
| No hardcoded selectors/routes/workflows | passes | `rg -n "hh\.ru\|data-qa\|/vacancy" src` без совпадений; tests содержат отдельные запреты на эти строки. | В synthetic HTML fixtures есть локальные страницы и тексты для тестов; это не site-specific production workflow. |
| No hardcoded internal URLs | passes | `run --live` принимает `--start-url`; HH.ru команды в docs стартуют с URL пользователя; `src` scan по HH/routes пустой. | Demo local URLs `127.0.0.1` генерируются для тестов и replay. |
| Semantic observation | passes | `src/scout_pilot/observation/semantic.py`; `tests/test_observation_engine.py`; `browser.observe` в live run вернул секции, интерактивные элементы и form summaries без raw HTML. | Наблюдение остается compact summary, поэтому может потерять декоративные детали страницы. |
| No complete HTML in LLM context | passes | raw HTML boundary scan без model-facing совпадений; `tests/test_models.py`, `tests/test_runtime_report.py`, `tests/test_replay_summary.py`; `PageObservation.to_llm_context()`. | Safety keys вроде `raw_html_included: False` есть в reporting sanitizer, но не являются HTML payload. |
| Context budgeting | passes | `replay-summary` показал context `1869 -> 663` токенов; `src/scout_pilot/context`; tests for oversized/repeated content. | Token estimate fallback приблизительный, не provider-native tokenizer. |
| Memory/reflection advanced pattern | passes | `src/scout_pilot/memory`, `src/scout_pilot/intelligence`, runtime reflection events; full pytest `179 passed`. | Memory пока in-memory/bounded, без vector DB, что соответствует текущему scope. |
| Retry and recovery | passes | `src/scout_pilot/intelligence`, `src/scout_pilot/navigation`, tests for stale/ambiguous/dynamic cases; live-local-demo сделал `browser.resolve_target` на ambiguous `Details`. | Видео может не показать retry, если happy path проходит без сбоев; для защиты есть тесты и synthetic blockers. |
| Security confirmation before side effects | passes | `live-local-demo` остановился перед `Apply`; CLI вывел ID подтверждения, риск, цель, очищенные аргументы и отмену; `src/scout_pilot/security`; security tests. | В non-interactive проверке prompt получил EOF и задача остановилась, что ожидаемо. В интерактивном видео нужно ответить `нет` или `да` вручную. |
| Report/replay safety | passes | `replay-summary` прочитал artifact; tests for raw HTML/secrets/private paths; `src/scout_pilot/reporting`. | Reports создаются в ignored `reports/tmp`; reviewer должен смотреть summary или sanitized JSON, а не приватные локальные артефакты. |
| Russian user-facing UX | passes | CLI outputs from `doctor`, `run`, `profile-info`, confirmation prompt and replay summary are Russian; docs are Russian. | Internal event names, identifiers and structured logs remain English by design. |
| Demo video path | passes | `docs/video_script.md`; validated commands for `doctor`, `live-local-demo`, `replay-summary`, tests and Ruff. | Main reliable video path is local mock demo; live HH.ru must be presented as manual smoke. |
| HH.ru demo path | partial | `docs/hh_demo.md`, `docs/video_script.md`, `scout-pilot run ... --start-url https://hh.ru ...` documented; no HH-specific source hardcoding found. | Live HH.ru smoke was not run in this pass and can stop on CAPTCHA/login/region/markup changes. |
| Clean GitHub repository and commits | passes | `git rev-parse --short HEAD` returned `891b10e` before this review edit; `git ls-remote origin refs/heads/main` returned the same commit; generated profiles/reports/caches are ignored. | This review is documentation-only; generated validation artifacts stay ignored. |

## Residual risks

- Live provider integration is implemented, but not manually smoke-tested here without a local API key.
- Live HH.ru behavior remains outside deterministic validation and should not be promised as guaranteed.
- `mock` provider proves runtime wiring, not model quality.
- Arbitrary real websites can still fail on CAPTCHA, login walls, A/B markup, slow loading or inaccessible labels.
- Context budgeting uses safe deterministic estimates; exact provider token accounting can differ.

## Interview defense notes

Use this order in the interview:

1. Show `scout-pilot doctor`.
2. Show `scout-pilot browser-smoke --headed --hold-seconds 0.1` or the visible `live-local-demo`.
3. Run `scout-pilot live-local-demo --headed --slow-mo-ms 120 --dashboard verbose`.
4. Point to tool calls, sanitized args, context metrics and Security Policy pause before `Apply`.
5. Show `scout-pilot replay-summary reports/tmp/live-local-demo-replay.json`.
6. Explain that HH.ru is a manual smoke target from a user-provided URL, not a hardcoded workflow.
7. Do not claim live OpenAI/Anthropic was verified unless `provider-smoke` succeeds with a local `.env`.

## Decision

Decision: **passes with concerns**.

The concerns are bounded and honest: live provider smoke and live HH.ru smoke require local/manual validation. The repository itself now has enough implementation evidence, tests and demo material to defend the assignment cleanly.
