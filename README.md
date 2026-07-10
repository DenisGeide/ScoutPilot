# Scout Pilot

Scout Pilot — учебный автономный браузерный агент для интервью-проекта. Репозиторий показывает, как можно аккуратно разложить браузерную автоматизацию, LLM-провайдера, планирование, память, безопасность и отчеты по понятным слоям.

Проект не заявляет production-ready статус. Это рабочая основа: ее можно установить, запустить локальные тесты, посмотреть dry-run CLI и выполнить ручной smoke-тест на HH.ru без hardcoded selectors и без автоматической отправки заявок.

## Что уже есть

- Browser Engine на Playwright с persistent profile, navigation, screenshots, cleanup и structured failures.
- Semantic Observation Engine: компактное представление страницы без полного HTML и значений чувствительных полей.
- Provider-neutral Tool Runtime с валидацией, history, timeout handling и Security Policy перед выполнением действий.
- LLM Provider Layer для OpenAI/Anthropic за общим интерфейсом. В автоматических тестах используются только mocks.
- Planning Engine, Hierarchical Memory, Context Budgeting, Execution Intelligence и Autonomous Agent Runtime.
- Generic semantic navigation без CSS selectors, XPath, hardcoded URLs и site-specific workflows.
- CLI на русском: `status`, `run --dry-run`, `interactive`, `browser-smoke`, `interview-demo`, `demo-vacancy-search`.
- Безопасные JSON report/replay артефакты без raw HTML, cookies, tokens, browser profiles и приватных путей.

## Ограничения

- Обычный `scout-pilot run` сейчас работает как dry-run. Он показывает ход выполнения и пишет отчет, но не делает live LLM-вызовы и не управляет браузером.
- HH.ru используется только для ручного smoke-теста. Автоматические тесты не зависят от живого сайта.
- Live HH.ru может показать CAPTCHA, вход, выбор региона или другую динамическую страницу. Это честный результат smoke-теста, а не повод подделывать успех.
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
- [Interview demo](docs/interview_demo.md)
- [Ручной smoke-тест HH.ru](docs/hh_demo.md)
- [Заметки для разработки](docs/development.md)
- [Как вносить изменения](CONTRIBUTING.md)

## Interview Demo

Для короткого видео используйте локальный deterministic demo:

```powershell
scout-pilot interview-demo --headed --slow-mo-ms 120
```

Команда сама создает synthetic site в `reports/tmp/`, открывает видимый браузер, читает три страницы, пишет report/replay и показывает security pause перед действием `Apply`. Подробный чек-лист: [docs/interview_demo.md](docs/interview_demo.md).

## Демо HH.ru

Демо-команда начинает с URL, который передает пользователь, и дальше использует только семантические наблюдения и обнаруженные ссылки. В коде нет маршрутов HH.ru, CSS selectors или XPath под сайт.

Локальная детерминированная проверка демо:

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
