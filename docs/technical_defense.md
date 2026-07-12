# Техническая защита проекта

Этот документ нужен для интервью: по нему можно коротко объяснить, что построено, почему архитектура устроена именно так и где у проекта честные ограничения.

## Кратко о задании

Нужно было собрать автономного браузерного агента, который:

- запускает видимый браузер и поддерживает persistent session;
- принимает задачу на естественном языке;
- работает через Playwright, но без hardcoded CSS selectors, XPath, внутренних маршрутов и workflow под конкретный сайт;
- использует OpenAI или Anthropic через заменяемый provider layer;
- не отправляет LLM полный HTML, DOM dumps, cookies, tokens, browser profiles и приватные файлы;
- строит семантическое наблюдение страницы;
- контролирует размер model context;
- умеет планировать, выполнять tools, оценивать прогресс и останавливаться;
- требует подтверждение перед отправкой форм, откликами, сообщениями и другими внешними side effects;
- демонстрирует HH.ru-like vacancy search без автоматической отправки заявок;
- показывает пользовательский прогресс на русском.

## Что реально реализовано

В репозитории есть рабочая основа Scout Pilot:

- Browser Engine на Playwright с navigation, screenshots, persistent profile, ручным `profile-open` flow и cleanup;
- Semantic Observation Engine с компактной моделью страницы;
- Tool Runtime без привязки к провайдеру, с валидацией, timeout handling, history и security hook;
- LLM Provider Layer для OpenAI и Anthropic с отдельными schema adapters; локальный Codex CLI подключен через тот же provider-neutral контракт и не выполняет браузерные действия напрямую;
- Planning Engine, Hierarchical Memory, Context Budgeting, Execution Intelligence и Autonomous Agent Runtime;
- deterministic Security Policy с confirmation flow;
- семантическая навигация по observation IDs, roles, names и visible context;
- CLI на русском, `scout-pilot run --live`, `provider-smoke`, локальное `live-local-demo`, scripted `interview-demo`, ручной HH.ru smoke flow, JSON report/replay;
- детерминированные тесты на локальных тестовых страницах и mocked providers.

Важно: `scout-pilot run` по умолчанию остается безопасным dry-run, но с флагом `--live` запускает основной автономный runtime loop: видимый браузер, semantic observation, planning/reasoning, Tool Runtime, Security Policy, memory, context budgeting, reflection и безопасные report/replay. Для локальной воспроизводимой проверки можно использовать `--provider mock`; OpenAI/Anthropic требуют локальные ключи вне репозитория, а `--provider codex` использует локальный Codex CLI с авторизацией через ChatGPT.

## Архитектура

Проект разделен на независимые слои:

- `scout_pilot.browser` управляет Playwright и не отдает наружу `Page`, `Browser`, `Context` или DOM handles.
- `scout_pilot.observation` превращает состояние страницы в безопасное semantic observation.
- `scout_pilot.tools` выполняет tools без привязки к провайдеру и всегда вызывает Security Policy до browser action.
- `scout_pilot.llm` изолирует OpenAI/Anthropic SDK и provider-specific tool schema adapters.
- `scout_pilot.planning` строит план, но не исполняет browser actions.
- `scout_pilot.memory` хранит bounded working/task/episodic memory без секретов и полного HTML.
- `scout_pilot.context` собирает bounded model-facing payload.
- `scout_pilot.runtime` координирует observe-think-plan-act-evaluate loop.
- `scout_pilot.security` детерминированно классифицирует actions и требует подтверждения.
- `scout_pilot.reporting` пишет sanitized report/replay.
- `scout_pilot.demo` собирает демонстрационные сценарии поверх общих слоев.

Главная идея: LLM предлагает намерение, но браузер, безопасность, контекст и отчеты контролируются детерминированным кодом.

## Ключевые решения

### Почему Playwright изолирован

Playwright мощный, но низкоуровневый. Если отдавать `Page` или locators наружу, остальные слои быстро начнут зависеть от DOM, CSS selectors и timing details. Поэтому публичный API Browser Engine возвращает только контролируемые модели состояния и результаты действий. Это упрощает тесты, замену реализации и проверку правила "никаких hardcoded selectors в бизнес-логике".

### Почему semantic observations вместо raw HTML

Полный HTML слишком большой, шумный и часто содержит приватные данные: hidden inputs, tokens, email, session markers, значения форм. Semantic Observation Engine оставляет только то, что нужно для reasoning: title, URL origin, visible sections, interactive elements, roles, names, form summaries, issue signals и bounded text. Sensitive values редактируются. Это снижает риск утечки и делает context budgeting предсказуемым.

### Почему Tool Runtime не зависит от провайдера

Tool Runtime не знает про OpenAI или Anthropic. Он хранит нейтральные `ToolSchema`, `ToolRequest` и `ToolExecutionResult`. Provider-specific conversion вынесен в `OpenAIToolSchemaAdapter` и `AnthropicToolSchemaAdapter`. Благодаря этому tools не нужно переписывать при смене LLM-провайдера.

### Почему есть Context Budgeting

Даже semantic observation и memory могут разрастаться. `DeterministicContextBudgeter` оценивает размер payload, резервирует output budget, приоритизирует важные sections, удаляет повторяющиеся header/footer/navigation fragments и сжимает старые observations. При emergency compression сохраняются user goal, constraints, security warnings, confirmation decisions и recent failures.

### Почему Security Policy отдельная

Безопасность не доверяет LLM. Модель может предложить tool call, но не может сама объявить действие безопасным. Tool Runtime вызывает `DeterministicSecurityPolicy` до исполнения. Safe reading/navigation проходит, `file://` и `javascript:` navigation блокируется, sensitive/destructive/external-side-effect actions возвращают paused result с русским confirmation message. В интерактивном `run --live` пользователь видит действие, риск, очищенную цель и последствия; подтверждение разрешает только один конкретный запрос инструмента, а отмена завершает задачу без выполнения внешнего действия.

## Как показывать demo

Основной live-показ для интервью:

```powershell
scout-pilot run "Проверь страницу и подготовь краткий отчет" `
  --live `
  --provider mock `
  --start-url https://example.com `
  --headed `
  --dashboard verbose
```

Persistent session можно показать отдельно:

```powershell
scout-pilot profile-info
scout-pilot profile-open --profile default --start-url https://example.com --headed
```

Идея защиты простая: логин делает человек в видимом браузере, профиль остается локально в ignored `.browser-profiles/`, а последующий `run --live` использует тот же default profile. Проект не автоматизирует ввод пароля и не экспортирует storage state.

Локальный сценарий без внешних сайтов и live LLM-вызовов, но через обычный runtime:

```powershell
scout-pilot live-local-demo --headed --slow-mo-ms 120 --dashboard compact
```

Что важно проговорить:

- сайт локальный и тестовый, поэтому demo не зависит от CAPTCHA или чужой разметки;
- браузер видимый, профиль persistent и исключен из Git;
- поиск и открытие страниц идут через semantic tools, а не через selectors;
- на динамичных страницах stale element ID не считается тупиковым крашем: tool повторно наблюдает страницу, remap-ит кандидат по semantic fingerprint и останавливается на ambiguity, если безопасно выбрать нельзя;
- CAPTCHA, login wall, cookie/banner overlay, region prompt и modal dialog попадают в `PageIssue`; CAPTCHA/login wall останавливают runtime честно до LLM/tools, а blocker-событие сохраняется в report/replay без raw HTML;
- выбранные tools, очищенные аргументы и Security Policy видны в terminal dashboard;
- перед действием `Apply` появляется security pause;
- report/replay сохраняются без raw HTML, cookies, tokens и browser profile data.

Ручной HH.ru smoke отделен:

```powershell
scout-pilot demo-vacancy-search `
  --start-url https://hh.ru `
  --query "AI Engineer Python AI Developer" `
  --max-vacancies 3 `
  --headed `
  --confirm-search-fill `
  --report-path reports/tmp/hh-demo-report.json
```

HH.ru может показать CAPTCHA, вход, выбор региона или измененную страницу. Это ожидаемый результат smoke-теста, а не повод подделывать успешный отчет.

Ручная проверка live LLM-провайдера отделена от браузерного demo:

```powershell
scout-pilot provider-smoke --provider openai
```

Команда читает только локальный `.env`, проверяет наличие нужного ключа и отправляет короткий provider-neutral запрос без браузерного состояния, HTML, cookies, tokens, профилей и приватных файлов. Для Anthropic используется `--provider anthropic` и модель, совместимая с Anthropic.

## Тестовая стратегия

Автоматические тесты не ходят на HH.ru и не вызывают live OpenAI/Anthropic. Они используют:

- unit tests для моделей, планирования, memory, context, security и tool runtime;
- mocked providers и fake clients для LLM layer;
- локальные тестовые страницы для Browser Engine, observation, semantic navigation и demo flow;
- CLI parsing и report/replay sanitizer tests;
- regression tests для запрета raw HTML leaks и dangerous navigation.

Базовая проверка:

```powershell
python -m pytest
scout-pilot status
scout-pilot run "Проверить страницу" --live --provider mock --start-url https://example.com --headless --max-iterations 3 --dashboard off
scout-pilot browser-smoke --headless --hold-seconds 0
scout-pilot live-local-demo --headless --slow-mo-ms 0 --dashboard off
scout-pilot interview-demo --headless --slow-mo-ms 0 --wait-after-search-ms 50
```

Архитектурные границы удобно проверять так:

```powershell
rg -n "from playwright|import playwright" src\scout_pilot | rg -v "src\\scout_pilot\\browser"
rg -n "from openai|import openai|from anthropic|import anthropic" src\scout_pilot | rg -v "src\\scout_pilot\\llm"
rg -n "page\.content\(|inner_html|outer_html|innerHTML|outerHTML" src\scout_pilot
```

Отсутствие вывода означает, что явных нарушений не найдено.

## Честные ограничения

- `scout-pilot run --live` подключен к runtime loop, но качество произвольных live-задач зависит от выбранного провайдера, модели, сайта, доступности страницы и точности semantic observations.
- Live HH.ru не гарантирован: возможны CAPTCHA, login wall, региональные окна и A/B-разметка.
- LLM provider adapters покрыты mocks, а не live API tests, чтобы не требовать ключи и сеть в deterministic validation.
- Report safety зависит от sanitizer и regression tests; при добавлении новых fields нужно проверять, что туда не попадают HTML, tokens, cookies и private paths.
- Browser Engine напрямую умеет открывать `file://` для низкоуровневых local tests, но Tool Runtime блокирует такую навигацию перед автономным использованием.

## Практичное future work

- При необходимости расширить manual checklist для live provider smoke с разными моделями, не добавляя live-вызовы в CI.
- Расширить observability для разборов live-сбоев: больше безопасных reason codes в report/replay без raw HTML.
- Добавить больше локальных тестовых сайтов с другой разметкой, чтобы лучше проверять семантическую навигацию.

## Короткая формулировка для интервью

Scout Pilot не пытается быть боевым продуктом. Это проверяемая основа браузерного AI-агента: Playwright изолирован, LLM получает только безопасные semantic observations, tools не завязаны на конкретного провайдера, контекст ограничен, side effects проходят через deterministic Security Policy, а demo и тесты отделяют локальную проверку от нестабильного live HH.ru smoke.
