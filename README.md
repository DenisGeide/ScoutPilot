# Scout Pilot

Scout Pilot — учебный автономный браузерный агент для интервью-проекта. Цель репозитория — показать понятную, поддерживаемую архитектуру, которую Junior+/Middle Python AI developer сможет объяснить, защитить и развивать дальше.

На этом этапе реализованы фундамент проекта, Browser Engine, Semantic Observation Engine, provider-neutral Tool Runtime, LLM Provider Layer, Planning Engine, Hierarchical Memory, Autonomous Agent Runtime, Execution Intelligence и Context Budgeting: структура пакета, конфигурация, доменные модели, границы слоев, базовый CLI, документация, детерминированные тесты, изолированный слой Playwright, компактные семантические наблюдения страниц, нейтральные browser tools, pluggable OpenAI/Anthropic adapters, provider-neutral планирование задач, ограниченная иерархическая память, автономный observe-think-plan-act-evaluate цикл, детерминированная оценка результатов действий и бюджетированная сборка model-facing контекста. Независимая security policy и демонстрация HH.ru будут добавляться следующими этапами.

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

Ожидаемый результат CLI на текущем этапе — короткое русскоязычное сообщение о том, что фундамент, Browser Engine, Semantic Observation Engine, Tool Runtime, LLM Provider Layer, Planning Engine, Hierarchical Memory, Autonomous Agent Runtime, Execution Intelligence и Context Budgeting готовы. Live LLM-вызовы и полноценный автономный запуск из CLI пока не включены.

Для локальной smoke-проверки видимого браузера:

```powershell
scout-pilot browser-smoke --headed --hold-seconds 5
```

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
- Независимый Security Policy Layer пока не реализован, но Tool Runtime уже поддерживает pre-execution hook.
- HH.ru пока не используется.
- Полные HTML-страницы, DOM-дампы и значения чувствительных полей не входят в публичные модели.

## Следующий этап

Следующий prompt может развить Security Policy, reporting/replay или CLI-запуск поверх готового runtime loop.
