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

- Browser Engine на Playwright с navigation, screenshots, persistent profile и cleanup;
- Semantic Observation Engine с компактной моделью страницы;
- provider-neutral Tool Runtime с валидацией, timeout handling, history и security hook;
- LLM Provider Layer для OpenAI и Anthropic с отдельными schema adapters;
- Planning Engine, Hierarchical Memory, Context Budgeting, Execution Intelligence и Autonomous Agent Runtime;
- deterministic Security Policy с confirmation flow;
- generic semantic navigation по observation IDs, roles, names и visible context;
- CLI на русском, локальное `interview-demo`, ручной HH.ru smoke flow, JSON report/replay;
- детерминированные тесты на synthetic pages и mocked providers.

Важно: обычный `scout-pilot run` сейчас остается безопасным dry-run. Браузерное end-to-end поведение демонстрируется через `interview-demo` и `demo-vacancy-search`, а не через полноценный live LLM CLI.

## Архитектура

Проект разделен на независимые слои:

- `scout_pilot.browser` управляет Playwright и не отдает наружу `Page`, `Browser`, `Context` или DOM handles.
- `scout_pilot.observation` превращает состояние страницы в безопасное semantic observation.
- `scout_pilot.tools` выполняет provider-neutral tools и всегда вызывает Security Policy до browser action.
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

### Почему Tool Runtime provider-neutral

Tool Runtime не знает про OpenAI или Anthropic. Он хранит нейтральные `ToolSchema`, `ToolRequest` и `ToolExecutionResult`. Provider-specific conversion вынесен в `OpenAIToolSchemaAdapter` и `AnthropicToolSchemaAdapter`. Благодаря этому tools не нужно переписывать при смене LLM-провайдера.

### Почему есть Context Budgeting

Даже semantic observation и memory могут разрастаться. `DeterministicContextBudgeter` оценивает размер payload, резервирует output budget, приоритизирует важные sections, удаляет повторяющиеся header/footer/navigation fragments и сжимает старые observations. При emergency compression сохраняются user goal, constraints, security warnings, confirmation decisions и recent failures.

### Почему Security Policy отдельная

Безопасность не доверяет LLM. Модель может предложить tool call, но не может сама объявить действие безопасным. Tool Runtime вызывает `DeterministicSecurityPolicy` до исполнения. Safe reading/navigation проходит, `file://` и `javascript:` navigation блокируется, sensitive/destructive/external-side-effect actions возвращают paused result с русским confirmation message. Подтверждение разрешает только один exact pending request.

## Как защищать demo

Основной показ для интервью:

```powershell
scout-pilot interview-demo --headed --slow-mo-ms 120
```

Что важно проговорить:

- сайт локальный и synthetic, поэтому demo детерминированное и не зависит от CAPTCHA или чужой разметки;
- браузер видимый, профиль persistent и исключен из Git;
- поиск и открытие страниц идут через semantic tools, а не через selectors;
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

HH.ru может показать CAPTCHA, вход, выбор региона или измененную страницу. Это честный результат smoke-теста, а не повод подделывать успешный отчет.

## Тестовая стратегия

Автоматические тесты не ходят на HH.ru и не вызывают live OpenAI/Anthropic. Они используют:

- unit tests для моделей, планирования, memory, context, security и tool runtime;
- mocked providers и fake clients для LLM layer;
- local synthetic pages для Browser Engine, observation, semantic navigation и demo flow;
- CLI parsing и report/replay sanitizer tests;
- regression tests для запрета raw HTML leaks и dangerous navigation.

Базовая проверка:

```powershell
python -m pytest
scout-pilot status
scout-pilot browser-smoke --headless --hold-seconds 0
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

- CLI `run` пока не является полноценным live autonomous LLM/browser режимом; он dry-run.
- Live HH.ru не гарантирован: возможны CAPTCHA, login wall, региональные окна и A/B-разметка.
- LLM provider adapters покрыты mocks, а не live API tests, чтобы не требовать ключи и сеть в deterministic validation.
- Report safety зависит от sanitizer и regression tests; при добавлении новых fields нужно проверять, что туда не попадают HTML, tokens, cookies и private paths.
- Browser Engine напрямую умеет открывать `file://` для низкоуровневых local tests, но Tool Runtime блокирует такую навигацию перед автономным использованием.

## Практичное future work

- Подключить полноценный live `scout-pilot run` через уже существующие Runtime, Reasoning Engine и Tool Runtime, сохранив security confirmations.
- Добавить небольшой manual checklist для live provider smoke с тестовым ключом вне репозитория.
- Расширить observability для разборов live-сбоев: больше безопасных reason codes в report/replay без raw HTML.
- Добавить больше synthetic sites с другой разметкой, чтобы усилить уверенность в generic semantic navigation.

## Короткая формулировка для интервью

Scout Pilot не пытается быть production platform. Это проверяемая архитектурная основа браузерного AI-агента: Playwright изолирован, LLM получает только безопасные semantic observations, tools provider-neutral, контекст ограничен, side effects проходят через deterministic Security Policy, а demo и тесты честно разделяют deterministic local validation и нестабильный live HH.ru smoke.
