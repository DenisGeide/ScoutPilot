# Scout Pilot

Scout Pilot — учебный автономный браузерный агент для интервью-проекта. Цель репозитория — показать понятную, поддерживаемую архитектуру, которую Junior+/Middle Python AI developer сможет объяснить, защитить и развивать дальше.

На этом этапе реализованы фундамент проекта, Browser Engine и Semantic Observation Engine: структура пакета, конфигурация, доменные модели, границы слоев, базовый CLI, документация, детерминированные тесты, изолированный слой Playwright и компактные семантические наблюдения страниц. LLM-провайдеры и демонстрация HH.ru будут добавляться следующими этапами.

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

Ожидаемый результат CLI на текущем этапе — короткое русскоязычное сообщение о том, что фундамент, Browser Engine и Semantic Observation Engine готовы, а LLM-вызовы еще не включены.

Для локальной smoke-проверки видимого браузера:

```powershell
scout-pilot browser-smoke --headed --hold-seconds 5
```

## Текущий статус

- Код и внутренние идентификаторы написаны на английском.
- Пользовательский CLI и документация написаны на русском.
- Playwright подключен только внутри Browser Engine.
- Semantic Observation Engine получает только контролируемый snapshot от Browser Engine.
- OpenAI и Anthropic пока не вызываются.
- HH.ru пока не используется.
- Полные HTML-страницы, DOM-дампы и значения чувствительных полей не входят в публичные модели.

## Следующий этап

Следующий prompt может реализовать Tool Runtime поверх существующих границ без прямого импорта Playwright.
