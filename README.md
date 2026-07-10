# Scout Pilot

Scout Pilot — учебный автономный браузерный агент для интервью-проекта. Репозиторий показывает, как разнести браузерную автоматизацию, LLM-провайдера, планирование, память, безопасность и отчеты по понятным слоям.

Это не готовый продукт для реальной эксплуатации. Репозиторий можно установить, проверить локальными тестами, посмотреть CLI в dry-run режиме, запустить live-цикл через `scout-pilot run --live` и вручную выполнить smoke-тест на HH.ru без hardcoded selectors и без автоматической отправки заявок.

## Что уже есть

- Browser Engine на Playwright с persistent profile, navigation, screenshots, cleanup и structured failures.
- Semantic Observation Engine: компактное представление страницы без полного HTML и значений чувствительных полей.
- Provider-neutral Tool Runtime с валидацией, history, timeout handling и Security Policy перед выполнением действий.
- LLM Provider Layer для OpenAI/Anthropic за общим интерфейсом. В автоматических тестах используются только mocks.
- Planning Engine, Hierarchical Memory, Context Budgeting, Execution Intelligence и Autonomous Agent Runtime.
- Generic semantic navigation без CSS selectors, XPath, hardcoded URLs и site-specific workflows.
- CLI на русском: `status`, `run --dry-run`, `run --live`, `provider-smoke`, `interactive`, `browser-smoke`, `interview-demo`, `demo-vacancy-search`.
- Compact/verbose dashboard показывает задачу, состояние, итерацию, шаг плана, краткое наблюдение, выбранный tool, очищенные аргументы, решение Security Policy и результат.
- Безопасные JSON report/replay артефакты без raw HTML, cookies, tokens, browser profiles, чувствительных значений и приватных путей.

## Ограничения

- `scout-pilot run --live` запускает настоящий runtime loop: видимый браузер, semantic observation, planning/reasoning, Tool Runtime, Security Policy, reflection и report/replay. Для воспроизводимой проверки есть `--provider mock`; для OpenAI/Anthropic нужны локальные API-ключи в `.env`.
- HH.ru используется только для ручного smoke-теста. Автоматические тесты не зависят от живого сайта.
- Live HH.ru может показать CAPTCHA, вход, выбор региона или другую динамическую страницу. Это нормальный результат smoke-теста; его не нужно подменять успешным сценарием.
- Файл `LICENSE` не добавлен, потому что владелец проекта пока не выбрал лицензию.

## Быстрый старт

Требования: Python 3.11+ и установленный браузер Chromium через Playwright.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Создайте локальный `.env` из безопасного примера:

```powershell
Copy-Item .env.example .env
```

Проверьте установку:

```powershell
python -m pytest
scout-pilot status
```

Запустите безопасный CLI dry-run:

```powershell
scout-pilot run "Найди три подходящие вакансии Python AI Developer" --dry-run
```

Запустите live-режим без внешних LLM-вызовов, но с реальным браузером и runtime:

```powershell
scout-pilot run "Проверь страницу и подготовь краткий отчет" `
  --live `
  --provider mock `
  --start-url https://example.com `
  --headed `
  --dashboard verbose
```

В `compact` и `verbose` режимах терминал показывает безопасную трассу tool-вызовов. Значения форм, cookies, tokens, API keys, raw HTML и приватные пути редактируются перед выводом и перед записью report/replay.

Для live-режима с OpenAI или Anthropic добавьте ключ в локальный `.env` и выберите провайдера:

```powershell
scout-pilot run "Проверь страницу и подготовь краткий отчет" `
  --live `
  --provider openai `
  --start-url https://example.com `
  --headed `
  --dashboard verbose
```

Ручная проверка live-провайдера без браузера и без приватного контекста:

```powershell
scout-pilot provider-smoke --provider openai
```

Для Anthropic используйте `--provider anthropic` и совместимую модель в локальном `.env`. Автоматические тесты эти команды не вызывают.

Проверка браузера без живых сайтов:

```powershell
scout-pilot browser-smoke --headless --hold-seconds 0
```

Локальное interview demo без реальных сайтов и учетных данных:

```powershell
scout-pilot interview-demo --headless --slow-mo-ms 0 --wait-after-search-ms 50
```

## Документация

- [Установка и конфигурация](docs/setup.md)
- [Архитектура](docs/architecture.md)
- [Тестирование](docs/testing.md)
- [Техническая защита проекта](docs/technical_defense.md)
- [Release checklist](docs/release_checklist.md)
- [Interview demo](docs/interview_demo.md)
- [Ручной smoke-тест HH.ru](docs/hh_demo.md)
- [Заметки для разработки](docs/development.md)
- [Как вносить изменения](CONTRIBUTING.md)

## Interview Demo

Для короткого видео используйте локальный deterministic demo:

```powershell
scout-pilot interview-demo --headed --slow-mo-ms 120
```

Команда сама создает локальный тестовый сайт в `reports/tmp/`, открывает видимый браузер, читает три страницы, пишет report/replay и показывает остановку безопасности перед действием `Apply`. Подробный чек-лист: [docs/interview_demo.md](docs/interview_demo.md).

## Демо HH.ru

Демо-команда начинает с URL, который передает пользователь, и дальше использует только семантические наблюдения и обнаруженные ссылки. В коде нет маршрутов HH.ru, CSS selectors или XPath под сайт.

Локальная проверка демо:

```powershell
python -m pytest tests/test_demo_vacancy_search.py
```

Ручной live smoke:

```powershell
scout-pilot demo-vacancy-search `
  --start-url https://hh.ru `
  --query "AI Engineer Python AI Developer" `
  --max-vacancies 3 `
  --headed `
  --confirm-search-fill `
  --report-path reports/tmp/hh-demo-report.json
```

Если запуск поиска выглядит как отправка формы, CLI остановится и попросит подтверждение. Для демо подтверждайте только запуск поиска, не отклики и не сообщения:

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

Подробный чек-лист: [docs/hh_demo.md](docs/hh_demo.md).

## Безопасность данных

Не коммитьте `.env`, browser profiles, session state, cookies, tokens, приватные скриншоты, временные отчеты и реальные резюме. `.gitignore` уже закрывает типовые локальные артефакты:

- `.env`, `.venv`, caches;
- `.browser-profiles/`, `.browser-sessions/`, `storage-state*.json`, `cookies*.json`, `tokens*.json`;
- `reports/tmp/`, `reports/private/`, приватные screenshots и `.har`.

Перед коммитом полезно проверить:

```powershell
git status --short
git diff --check
python -m pytest
```

## Структура

```text
src/scout_pilot/
  browser/       # изоляция Playwright
  observation/   # семантические наблюдения страницы
  tools/         # provider-neutral tool runtime
  llm/           # адаптеры OpenAI/Anthropic и reasoning
  planning/      # создание и пересмотр планов
  memory/        # ограниченная иерархическая память
  runtime/       # автономный цикл и state machine
  security/      # детерминированная политика действий
  navigation/    # разрешение семантических целей
  reporting/     # безопасные отчеты и replay
  cli/           # пользовательский CLI на русском
tests/           # детерминированные unit/integration tests
docs/            # документация на русском
```
