# Acceptance review

Дата: 2026-07-10.

Источник требований: `promts/codex_pack_final/ORIGINAL_ASSIGNMENT.md`, `promts/codex_pack_final/REQUIREMENTS_MATRIX.md`, README и текущая проектная документация. Папка `promts/` намеренно ignored и не является частью публичного репозитория.

## Итог

Статус: **проходит с явно описанными ограничениями**.

Критических несоответствий, которые можно безопасно исправить маленьким изменением в рамках этой проверки, не найдено. Проект закрывает основную архитектуру, безопасность, локальный demo path и тестовую базу. Оставшиеся ограничения описаны в README, technical defense и release checklist.

Главное ограничение теперь не в наличии live CLI, а в честной демонстрации его границ: `scout-pilot run --live` подключен к основному runtime loop, но автоматические тесты используют mock provider и локальные страницы. Реальные OpenAI/Anthropic вызовы и live HH.ru smoke остаются ручными проверками, потому что требуют ключей, сети и могут остановиться на CAPTCHA, входе или измененной странице.

## Requirements matrix

| Требование | Статус | Доказательства | Примечания |
|---|---|---|---|
| Visible browser launch | Проходит | `scout-pilot browser-smoke --headed`, `scout-pilot interview-demo --headed`, `PlaywrightBrowserEngine` | Headless режим доступен для CI/local checks. |
| Persistent browser sessions | Проходит | `BrowserEngineConfig.user_data_dir`, `.browser-profiles/` в `.gitignore`, docs/interview demo profile | Профили локальные и не коммитятся. |
| Natural-language task input | Проходит | `scout-pilot run "..." --dry-run`, `scout-pilot run "..." --live`, `interactive`, `UserTask` models | Live режим можно запускать с `--provider mock` без внешних API. |
| Autonomous multi-step browser actions | Проходит с оговоркой | `AutonomousAgentRuntime` tests, `run --live --provider mock`, `interview-demo`, `demo-vacancy-search` multi-step flow | Runtime loop подключен к CLI; качество реальных сайтов зависит от провайдера и страницы. |
| OpenAI or Anthropic provider support | Проходит | `OpenAILlmProvider`, `AnthropicLlmProvider`, `MockLlmProvider`, provider tests | Automated tests use mocks; live API calls are intentionally not CI evidence. |
| Playwright automation | Проходит | `scout_pilot.browser`, browser smoke, local Playwright tests | Playwright imports are isolated to Browser Engine. |
| No hardcoded CSS selectors or XPath in app workflows | Проходит | semantic navigation tools, boundary scans, tests on local test sites | Browser Engine uses generic Playwright locators internally; demo layer has no site-specific selectors. |
| No hardcoded website workflows | Проходит | `demo-vacancy-search` starts from user URL; code scan for HH routes/selectors in `src/scout_pilot/demo` | HH.ru appears in docs/manual command examples only. |
| No hardcoded internal URLs/routes | Проходит | URL policy in docs, demo starts from `--start-url`, tests use local fixtures | Test URLs are fixtures, not production workflow routes. |
| No complete HTML in LLM context | Проходит | `PageObservation`, `DeterministicContextBudgeter`, raw HTML boundary scans/tests | No public tool returns full HTML. |
| Semantic observations | Проходит | `SemanticObservationEngine`, observation models and tests | Sensitive form values are redacted. |
| Context budgeting | Проходит | `DeterministicContextBudgeter`, before/after metrics in demo/reporting, tests | Uses fallback token estimates, not provider-only counters. |
| Advanced pattern: memory/reflection | Проходит | hierarchical memory, execution intelligence, runtime reflection events/tests | Ограниченная in-memory реализация, без vector DB по решению проекта. |
| Retry and adaptive recovery | Проходит | execution intelligence tests, runtime failure/retry limits, stale element recovery tests | Demo may not always show recovery unless a failure path occurs. |
| Confirmation before destructive/external side effects | Проходит | deterministic Security Policy, Tool Runtime pre-execution hook, confirmation tests | `file://` navigation is blocked; submit/apply/send-like actions pause. |
| HH.ru demo path | Проходит с оговоркой | `demo-vacancy-search`, `docs/hh_demo.md`, local vacancy-flow tests | Live HH.ru smoke is manual and may stop on CAPTCHA/login/region. |
| Russian user-facing UX | Проходит | CLI progress/errors/confirmation messages, README/docs in Russian | Internal structured logs remain English. |
| English source/internal logs | Проходит | package/module/code naming, structured log formatter, tests/docs policy | Russian appears in user-facing CLI and docs. |
| Clean GitHub repository and incremental commits | Частично | clean local Git history, release checklist, `.gitignore` | Local repository is clean after checkpoints; `git remote -v` is empty until owner adds GitHub URL. |
| Short demo/replay materials | Проходит | `interview-demo` generates report/replay in ignored `reports/tmp/`; docs provide recording checklist | Очищенные артефакты генерируются локально и не коммитятся. |

## Residual risks

- Live CLI проверяется детерминированно через mock provider; реальные provider calls требуют локальных ключей и ручной smoke-проверки.
- Если GitHub authentication недоступна локально, push должен выполнить владелец репозитория.
- Live HH.ru behavior is not deterministic. CAPTCHA, login, region selection or A/B markup can stop the smoke flow.
- Live provider calls are not part of automated validation. Provider adapters are covered with mocks to avoid secrets and network dependency.
- Report safety relies on sanitizer coverage. New report fields must keep regression tests for raw HTML, tokens, cookies and private paths.

## Acceptance decision

The repository is acceptable for interview review as a clean, defensible Junior+/Middle Python AI developer project if presented honestly:

- show local deterministic tests and `interview-demo`;
- explain that HH.ru is a manual smoke target, not CI;
- показать `scout-pilot run --live --provider mock --start-url <URL>` как основной CLI-путь и честно отделить его от ручных live provider/HH.ru проверок;
- do not claim production readiness or guaranteed live HH.ru success.

Этот отчет отражает текущее состояние после подключения live CLI к основному runtime loop.
