# Scout Pilot

Scout Pilot — учебный автономный браузерный агент для интервью-проекта. Цель репозитория — показать понятную, поддерживаемую архитектуру, которую Junior+/Middle Python AI developer сможет объяснить, защитить и развивать дальше.

На этом этапе реализованы фундамент проекта, Browser Engine, Semantic Observation Engine, provider-neutral Tool Runtime, LLM Provider Layer, Planning Engine, Hierarchical Memory, Autonomous Agent Runtime, Execution Intelligence, Context Budgeting, Independent Security Policy Layer, Universal Semantic Navigation и end-to-end demo/reporting слой: структура пакета, конфигурация, доменные модели, границы слоев, базовый CLI, документация, детерминированные тесты, изолированный слой Playwright, компактные семантические наблюдения страниц, нейтральные browser tools, pluggable OpenAI/Anthropic adapters, provider-neutral планирование задач, ограниченная иерархическая память, автономный observe-think-plan-act-evaluate цикл, детерминированная оценка результатов действий, бюджетированная сборка model-facing контекста, независимая проверка действий перед Tool Runtime, generic semantic navigation без hardcoded routes/selectors и демонстрационный поиск вакансий с безопасным JSON-отчетом.

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Скопируйте пример конфигурации и заполните значения только локально:

```powershell
Copy-Item .env.example .env
```

Не добавляйте `.env`, профили браузера, cookies, session state, приватные скриншоты и временные отчеты в Git.

## Проверка

```powershell
python -m pytest
scout-pilot status
```

Ожидаемый результат CLI на текущем этапе — короткое русскоязычное сообщение о том, что фундамент, Browser Engine, Semantic Observation Engine, Tool Runtime, LLM Provider Layer, Planning Engine, Hierarchical Memory, Autonomous Agent Runtime, Execution Intelligence, Context Budgeting и Security Policy готовы. Universal Semantic Navigation подключена внутри browser tools и покрыта локальными synthetic tests. Live LLM-вызовы и полноценный автономный запуск из CLI пока не включены.

Для локальной smoke-проверки видимого браузера:

```powershell
scout-pilot browser-smoke --headed --hold-seconds 5
```

## Демо поиска вакансий

Демо-команда не содержит маршрутов, CSS selectors или XPath под конкретный сайт. Она начинает с URL, который передает пользователь, ищет поле и кнопку поиска через семантическое наблюдение, открывает до трех найденных страниц по обнаруженным ссылкам, готовит короткие заметки и останавливается до отклика, сообщения или отправки формы.

Локальная детерминированная проверка использует synthetic pages:

```powershell
python -m pytest tests/test_demo_vacancy_search.py
```

Ручной live smoke на HH.ru:

```powershell
scout-pilot demo-vacancy-search `
  --start-url https://hh.ru `
  --query "AI Engineer Python AI Developer" `
  --max-vacancies 3 `
  --headed `
  --confirm-search-fill `
  --report-path reports/tmp/hh-demo-report.json
```

Если запуск поиска на живом сайте выглядит как отправка формы, CLI остановится с русским сообщением безопасности. Продолжайте только если осознанно разрешаете именно поиск:

```powershell
scout-pilot demo-vacancy-search `
  --start-url https://hh.ru `
  --query "AI Engineer Python AI Developer" `
  --max-vacancies 3 `
  --headed `
  --confirm-search-fill `
  --confirm-search-submit `
  --report-path reports/tmp/hh-demo-report.json
```

Флаг `--probe-security` полезен для локального демо: он проверяет, что кнопка отклика/отправки останавливается на подтверждении. На живом сайте его лучше включать только во время ручной проверки, когда вы готовы внимательно читать сообщения безопасности. Подтверждать отклик, сообщение или отправку заявки для демонстрации не нужно.

HH.ru может показать CAPTCHA, выбор региона, страницу входа или другой динамический экран. Это не считается успешным поиском, и команда должна честно записать состояние в отчет, а не создавать фальшивый результат.

Подробный чек-лист ручной проверки: [docs/hh_demo.md](docs/hh_demo.md).

## Текущий статус

- Код и внутренние идентификаторы написаны на английском.
- Пользовательский CLI и документация написаны на русском.
- Playwright подключен только внутри Browser Engine.
- Semantic Observation Engine получает только контролируемый snapshot от Browser Engine.
- Tool Runtime использует provider-neutral schemas и не содержит OpenAI/Anthropic adapter logic.
- OpenAI и Anthropic изолированы в LLM Provider Layer и не вызываются в автоматических тестах.
- Reasoning Engine получает только компактное observation, memory summaries, tool schemas, constraints и budget.
- Planning Engine строит и пересматривает короткие планы через provider-neutral LLM interface, валидирует шаги локально и не исполняет browser tools.
- Hierarchical Memory разделяет working, task и episodic memory, выдает bounded summaries и фильтрует приватные данные до сохранения.
- Autonomous Agent Runtime координирует observation, planning, reasoning, tool execution, memory и explicit termination через state machine.
- Execution Intelligence после каждого tool result сравнивает semantic observations, классифицирует прогресс, замечает no-op/repeated failures и рекомендует retry, observe again, replan, confirmation или stop.
- Context Budgeting оценивает размер model-facing payload, сжимает oversized observations и memory summaries, сохраняет критичные факты, удаляет повторяющийся boilerplate и публикует before/after метрики для runtime/debug.
- Independent Security Policy Layer детерминированно классифицирует safe, sensitive, destructive и external side-effect actions, требует явное подтверждение на русском и ведет audit trail до выполнения Tool Runtime.
- Universal Semantic Navigation разрешает намерения через semantic observation IDs, умеет находить generic search fields, планировать заполнение форм по labels/accessibility names, отличать неоднозначные цели и восстанавливаться после stale semantic IDs через повторное наблюдение.
- Демо поиска вакансий использует только пользовательский стартовый URL и обнаруженные на странице ссылки; HH.ru описан как ручной live smoke, а не как hardcoded workflow.
- JSON-отчет демо хранит безопасные observations, выбранные tools, security pauses и короткие заметки без полного HTML.
- Полные HTML-страницы, DOM-дампы и значения чувствительных полей не входят в публичные модели.

## Следующий этап

Следующий prompt может улучшить автономный CLI-режим или расширить replay/recording поверх готового безопасного демо.
