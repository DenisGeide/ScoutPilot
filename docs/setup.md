# Установка и конфигурация

Этот файл описывает локальный запуск проекта с нуля. Команды ниже написаны для PowerShell, потому что проект разрабатывался и проверялся в Windows-окружении. На Linux/macOS шаги те же, отличаются только команды активации виртуального окружения.

## Требования

- Python 3.11 или новее. Текущая локальная проверка выполнялась на Python 3.14.3.
- Доступ к установке Python-пакетов.
- Chromium, установленный через Playwright.
- Git, если вы планируете делать коммиты.

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Если нужен live-режим с OpenAI или Anthropic, установите SDK провайдеров:

```powershell
python -m pip install -e ".[dev,providers]"
```

Для полного локального набора можно использовать `.[all]`. Обычная установка `.[dev]` достаточна для тестов, mock provider и локальных demo.

Проверка:

```powershell
scout-pilot status
scout-pilot doctor
python -m pytest
```

`scout-pilot doctor` — это быстрая преддемо-проверка окружения. Команда проверяет Python, импорт пакета, Playwright, headless Chromium smoke через Browser Engine, `.env`, защиту browser profile и временных отчетов через `.gitignore`, а также состояние Git. Предупреждения вроде отсутствующего `.env` не считаются блокерами для mock/demo режима.

## Локальная конфигурация

Скопируйте пример:

```powershell
Copy-Item .env.example .env
```

`.env` нужен только локально и не должен попадать в Git. В нем можно оставить ключи провайдеров пустыми, пока вы не запускаете реальные LLM-вызовы.

Основные настройки:

| Переменная | Назначение | Значение по умолчанию |
|---|---|---|
| `SCOUT_PILOT_BROWSER_PROFILE_DIR` | Локальный persistent profile браузера | `.browser-profiles/default` |
| `SCOUT_PILOT_BROWSER_HEADLESS` | Запускать браузер без окна | `false` |
| `SCOUT_PILOT_BROWSER_DEFAULT_TIMEOUT_MS` | Таймаут обычных browser actions | `10000` |
| `SCOUT_PILOT_BROWSER_NAVIGATION_TIMEOUT_MS` | Таймаут навигации | `15000` |
| `SCOUT_PILOT_BROWSER_SCREENSHOTS_DIR` | Папка диагностических скриншотов | `reports/tmp/screenshots` |
| `SCOUT_PILOT_BROWSER_VIEWPORT_WIDTH` | Ширина viewport и headed-окна | `1000` |
| `SCOUT_PILOT_BROWSER_VIEWPORT_HEIGHT` | Высота viewport и headed-окна | `900` |
| `SCOUT_PILOT_LLM_PROVIDER` | Провайдер для live-режима, если не передан `--provider` | `openai` |
| `SCOUT_PILOT_LLM_MODEL` | Имя модели, совместимое с выбранным провайдером | `gpt-4.1-mini` |
| `OPENAI_API_KEY` | Локальный ключ OpenAI, если нужен | пусто |
| `ANTHROPIC_API_KEY` | Локальный ключ Anthropic, если нужен | пусто |
| `SCOUT_PILOT_REQUIRE_CONFIRMATION` | Требовать подтверждение опасных действий | `true` |
| `SCOUT_PILOT_MAX_CONTEXT_TOKENS` | Верхняя оценка бюджета контекста | `12000` |

## Быстрые команды

Безопасный dry-run CLI:

```powershell
scout-pilot run "Проверить страницу и подготовить краткий отчет" --dry-run
```

То же без dashboard, с внутренними JSON logs:

```powershell
scout-pilot --verbose run "Проверить страницу" --dry-run --dashboard off
```

Live-режим с настоящим браузером и детерминированным mock provider:

```powershell
scout-pilot run "Проверить страницу и подготовить краткий отчет" `
  --live `
  --provider mock `
  --start-url https://example.com `
  --headed `
  --dashboard verbose
```

В `compact` и `verbose` dashboard видно текущую задачу, состояние, итерацию, шаг плана, краткое наблюдение, выбранный инструмент, очищенные аргументы, решение безопасности, статус результата и следующее действие. Та же очищенная trace сохраняется в JSON report/replay.

Для OpenAI или Anthropic используйте тот же `--live`, но передайте `--provider openai` или `--provider anthropic` и добавьте соответствующий API-ключ в локальный `.env`.
Если SDK провайдера не установлен, повторите установку с extra `providers`.

Ручная smoke-проверка live-провайдера без браузера, HTML и приватных файлов:

```powershell
scout-pilot provider-smoke --provider openai
```

Для Anthropic команда такая же, но в `.env` нужен `ANTHROPIC_API_KEY` и модель, которую поддерживает Anthropic. Эта проверка опциональна и не входит в автоматические тесты.
Если нужно только проверить, что локальный ключ указан, без live-запроса к модели:

```powershell
scout-pilot doctor --provider openai
```

Интерактивный режим:

```powershell
scout-pilot interactive --dry-run
```

Проверка браузера:

```powershell
scout-pilot browser-smoke --headless --hold-seconds 0
```

## Persistent profile для ручного входа

Профиль браузера хранится локально и исключен из Git. Проверить текущий путь:

```powershell
scout-pilot profile-info
```

Открыть видимый браузер с default profile:

```powershell
scout-pilot profile-open --profile default --start-url https://example.com --headed
```

Демо-поток для сайтов с авторизацией:

1. Запустите `profile-open`.
2. Войдите на сайт вручную.
3. Закройте браузер или нажмите Enter в терминале.
4. Запустите `scout-pilot run --live` с тем же default profile.
5. Агент продолжит из локальной logged-in session, если сайт сохранил ее в профиле.

Проект не автоматизирует логин, не хранит пароли в коде и не экспортирует `storage-state` в репозиторий.

Локальное runtime demo:

```powershell
scout-pilot live-local-demo --headless --slow-mo-ms 0 --dashboard off
```

Scripted interview demo:

```powershell
scout-pilot interview-demo --headless --slow-mo-ms 0 --wait-after-search-ms 50
```

Ручной HH.ru smoke описан отдельно: [hh_demo.md](hh_demo.md).

## Где появляются локальные артефакты

- `reports/tmp/` — временные JSON reports/replay и диагностические файлы.
- `.browser-profiles/` — persistent profile браузера.
- `.pytest_cache/`, `__pycache__/` — кеши тестов и Python.

Эти пути исключены из Git. Если вы делаете checkpoint перед коммитом, проверьте:

```powershell
git status --short
git check-ignore -v reports/tmp/example.json .browser-profiles/default
```
