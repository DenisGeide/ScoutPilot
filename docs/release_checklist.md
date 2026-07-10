# Release checklist

Этот чек-лист нужен перед отправкой репозитория на GitHub или перед показом на интервью. Он не заменяет тесты и не автоматизирует релиз.

## Что проверено локально

Перед публикацией нужно запускать:

```powershell
python -m pip install -e .
python -m pip check
python -m compileall -q src tests
python -m pytest -q
scout-pilot status
scout-pilot run "Проверить страницу" --dry-run --dashboard off
scout-pilot run "Проверить страницу" --live --provider mock --start-url https://example.com --headless --max-iterations 3 --dashboard off
scout-pilot browser-smoke --headless --hold-seconds 0
scout-pilot interview-demo --headless --slow-mo-ms 0 --wait-after-search-ms 50
```

Опционально, только с локальным ключом в `.env`:

```powershell
scout-pilot provider-smoke --provider openai
```

Архитектурные границы:

```powershell
rg -n "from playwright|import playwright" src\scout_pilot | rg -v "src\\scout_pilot\\browser"
rg -n "from openai|import openai|from anthropic|import anthropic" src\scout_pilot | rg -v "src\\scout_pilot\\llm"
rg -n "page\.content\(|inner_html|outer_html|innerHTML|outerHTML" src\scout_pilot
rg -n "hh\.ru|/vacancy|/jobs|/search|xpath|queryselector|locator\(" src\scout_pilot\demo
```

Для этих boundary scans отсутствие вывода означает, что явных нарушений не найдено.

## Что не публикуется

В репозиторий не должны попадать:

- `.env`, API keys, tokens, cookies;
- `.venv`, caches, `*.egg-info`;
- `.browser-profiles/`, `.browser-sessions/`, storage state и session data;
- `reports/tmp/`, `reports/private/`, приватные screenshots и `.har`;
- live HH.ru evidence, если оно содержит приватные данные или raw HTML.

Очищенные demo/replay артефакты сейчас намеренно не хранятся в репозитории. Локальное `interview-demo` генерирует их заново в ignored `reports/tmp/`.

## GitHub перед публикацией

Перед push нужно проверить:

```powershell
git status --short --branch
git remote -v
git log -1 --oneline
```

Если `git remote -v` пустой, владелец проекта должен добавить свой GitHub URL:

```powershell
git remote add origin <GitHub repository URL>
git push -u origin main
```

Не нужно угадывать remote URL или публиковать репозиторий в чужой namespace.

## Честные release notes

Текущая версия в `pyproject.toml`: `0.1.0`.

Состояние проекта:

- готов для клонирования, чтения, локального запуска тестов и interview demo;
- не заявляет готовность к реальной эксплуатации;
- `scout-pilot run` по умолчанию остается dry-run, а `scout-pilot run --live` запускает основной runtime loop;
- live HH.ru smoke выполняется вручную и может остановиться на CAPTCHA, login, регионе или измененной странице;
- live LLM provider calls не входят в автоматические тесты; для них есть ручной `provider-smoke`.

Тег релиза стоит создавать только после настройки GitHub remote и явного решения владельца проекта.
