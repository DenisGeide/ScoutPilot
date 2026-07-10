# Acceptance review

Дата: 2026-07-10.

Источник требований: `promts/codex_pack_final/ORIGINAL_ASSIGNMENT.md`, `promts/codex_pack_final/REQUIREMENTS_MATRIX.md`, README и текущая проектная документация. Папка `promts/` намеренно ignored и не является частью публичного репозитория.

## Итог

Статус: **pass with documented limitations**.

Критических compliance gaps, которые можно безопасно исправить маленьким изменением в рамках acceptance review, не найдено. Проект закрывает основную архитектуру, безопасность, deterministic demo path и тестовую базу. Оставшиеся ограничения честно описаны в README, technical defense и release checklist.

Главное ограничение: обычный `scout-pilot run` пока работает как dry-run. Полноценный live autonomous LLM/browser режим не подключен к CLI; браузерное end-to-end поведение показывается через `interview-demo` и `demo-vacancy-search`. Это важно проговорить на интервью как границу текущей реализации, а не скрывать.

## Requirements matrix

| Requirement | Status | Evidence | Notes |
|---|---|---|---|
| Visible browser launch | Pass | `scout-pilot browser-smoke --headed`, `scout-pilot interview-demo --headed`, `PlaywrightBrowserEngine` | Headless режим доступен для CI/local checks. |
| Persistent browser sessions | Pass | `BrowserEngineConfig.user_data_dir`, `.browser-profiles/` в `.gitignore`, docs/interview demo profile | Профили локальные и не коммитятся. |
| Natural-language task input | Pass | `scout-pilot run "..." --dry-run`, `interactive`, `UserTask` models | Live browser execution for arbitrary `run` task remains a limitation. |
| Autonomous multi-step browser actions | Partial pass | `AutonomousAgentRuntime` tests, `interview-demo`, `demo-vacancy-search` multi-step flow | Runtime exists and is tested; generic live CLI loop is not wired. |
| OpenAI or Anthropic provider support | Pass | `OpenAILlmProvider`, `AnthropicLlmProvider`, `MockLlmProvider`, provider tests | Automated tests use mocks; live API calls are intentionally not CI evidence. |
| Playwright automation | Pass | `scout_pilot.browser`, browser smoke, local Playwright tests | Playwright imports are isolated to Browser Engine. |
| No hardcoded CSS selectors or XPath in app workflows | Pass | semantic navigation tools, boundary scans, tests on synthetic sites | Browser Engine uses generic Playwright locators internally; demo layer has no site-specific selectors. |
| No hardcoded website workflows | Pass | `demo-vacancy-search` starts from user URL; code scan for HH routes/selectors in `src/scout_pilot/demo` | HH.ru appears in docs/manual command examples only. |
| No hardcoded internal URLs/routes | Pass | URL policy in docs, demo starts from `--start-url`, tests use synthetic pages | Synthetic test URLs are fixtures, not production workflow routes. |
| No complete HTML in LLM context | Pass | `PageObservation`, `DeterministicContextBudgeter`, raw HTML boundary scans/tests | No public tool returns full HTML. |
| Semantic observations | Pass | `SemanticObservationEngine`, observation models and tests | Sensitive form values are redacted. |
| Context budgeting | Pass | `DeterministicContextBudgeter`, before/after metrics in demo/reporting, tests | Uses fallback token estimates, not provider-only counters. |
| Advanced pattern: memory/reflection | Pass | hierarchical memory, execution intelligence, runtime reflection events/tests | Bounded in-memory implementation, no vector DB by design. |
| Retry and adaptive recovery | Pass | execution intelligence tests, runtime failure/retry limits, stale element recovery tests | Demo may not always show recovery unless a failure path occurs. |
| Confirmation before destructive/external side effects | Pass | deterministic Security Policy, Tool Runtime pre-execution hook, confirmation tests | `file://` navigation is blocked; submit/apply/send-like actions pause. |
| HH.ru demo path | Pass with live caveat | `demo-vacancy-search`, `docs/hh_demo.md`, synthetic vacancy tests | Live HH.ru smoke is manual and may stop on CAPTCHA/login/region. |
| Russian user-facing UX | Pass | CLI progress/errors/confirmation messages, README/docs in Russian | Internal structured logs remain English. |
| English source/internal logs | Pass | package/module/code naming, structured log formatter, tests/docs policy | Russian appears in user-facing CLI and docs. |
| Clean GitHub repository and incremental commits | Partial pass | clean local Git history, release checklist, `.gitignore` | Local repository is clean after checkpoints; `git remote -v` is empty until owner adds GitHub URL. |
| Short demo/replay materials | Pass | `interview-demo` generates report/replay in ignored `reports/tmp/`; docs provide recording checklist | Sanitized artifacts are generated locally, not committed. |

## Residual risks

- `scout-pilot run --live` is intentionally unavailable. A reviewer expecting arbitrary live autonomous browsing from the main CLI will see this as a product gap.
- GitHub remote is not configured in the local repository. Publication requires the owner to run `git remote add origin <GitHub repository URL>` and push.
- Live HH.ru behavior is not deterministic. CAPTCHA, login, region selection or A/B markup can stop the smoke flow.
- Live provider calls are not part of automated validation. Provider adapters are covered with mocks to avoid secrets and network dependency.
- Report safety relies on sanitizer coverage. New report fields must keep regression tests for raw HTML, tokens, cookies and private paths.

## Acceptance decision

The repository is acceptable for interview review as a clean, defensible Junior+/Middle Python AI developer project if presented honestly:

- show local deterministic tests and `interview-demo`;
- explain that HH.ru is a manual smoke target, not CI;
- state that `run` is dry-run and full live arbitrary-task CLI is future work;
- do not claim production readiness or guaranteed live HH.ru success.

No source-code changes were made during this acceptance review.
