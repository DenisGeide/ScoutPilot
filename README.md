# Scout Pilot

Scout Pilot — учебный автономный браузерный агент для интервью-проекта. Цель репозитория — показать понятную, поддерживаемую архитектуру, которую Junior+/Middle Python AI developer сможет объяснить, защитить и развивать дальше.

На этом этапе реализованы фундамент проекта, Browser Engine, Semantic Observation Engine, provider-neutral Tool Runtime, LLM Provider Layer и Planning Engine: структура пакета, конфигурация, доменные модели, границы слоев, базовый CLI, документация, детерминированные тесты, изолированный слой Playwright, компактные семантические наблюдения страниц, нейтральные browser tools, pluggable OpenAI/Anthropic adapters и provider-neutral планирование задач. Независимая security policy, автономный runtime и демонстрация HH.ru будут добавляться следующими этапами.

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

Ожидаемый результат CLI на текущем этапе — короткое русскоязычное сообщение о том, что фундамент, Browser Engine, Semantic Observation Engine, Tool Runtime, LLM Provider Layer и Planning Engine готовы. Автономный runtime и live LLM-вызовы из CLI пока не включены.

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
- Независимый Security Policy Layer пока не реализован, но Tool Runtime уже поддерживает pre-execution hook.
- HH.ru пока не используется.
- Полные HTML-страницы, DOM-дампы и значения чувствительных полей не входят в публичные модели.

## Следующий этап

Следующий prompt может реализовать Autonomous Agent Runtime поверх Planning Engine, Reasoning Engine, observations и нейтральных tool schemas.
