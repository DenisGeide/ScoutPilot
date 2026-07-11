# Сценарий видео для интервью

Цель ролика: за 3-5 минут показать, что Scout Pilot запускается из терминала, управляет видимым браузером через общий runtime, пишет безопасный replay/report и останавливается перед внешним действием.

Лучше записывать локальное deterministic demo. Оно не требует API-ключей и не зависит от HH.ru, но проходит через обычные слои проекта: Browser Engine, Semantic Observation, Tool Runtime, Reasoning/Planning, Memory, Context Budgeting, Security Policy и Execution Intelligence.

## Перед записью

Откройте терминал в корне репозитория.

```powershell
python -m pip install -e ".[dev]"
python -m playwright install chromium
scout-pilot doctor
```

Если хотите показать ручную проверку реального провайдера:

```powershell
python -m pip install -e ".[dev,providers]"
scout-pilot provider-smoke --provider openai
```

API-ключ должен лежать только в локальном `.env`. Не показывайте `.env` в видео.

## План на 3-5 минут

### 0:00-0:35 - репозиторий и README

Показать корень проекта и коротко README:

```powershell
git status --short
Get-Content README.md -Encoding UTF8 | Select-Object -First 60
```

Сказать:

- проект слоистый, Playwright изолирован в Browser Engine;
- LLM-провайдеры изолированы, Tool Runtime не зависит от OpenAI/Anthropic;
- агент использует semantic observations вместо raw HTML;
- Security Policy останавливает Apply/submit/delete/payment;
- автоматические тесты локальные и детерминированные, live HH.ru остается ручным smoke.

### 0:35-1:00 - проверка окружения

```powershell
scout-pilot doctor
```

Пояснить, что `doctor` проверяет Python, импорт пакета, Playwright, headless Chromium smoke, `.env`, ignored browser profile/report paths и состояние Git. Отсутствие `.env` не блокирует mock demo.

### 1:00-3:40 - основной видимый browser demo

Для записи используйте видимый браузер и подробный dashboard:

```powershell
scout-pilot live-local-demo `
  --headed `
  --slow-mo-ms 120 `
  --dashboard verbose `
  --max-iterations 8 `
  --report-path reports/tmp/video-demo-report.json `
  --replay-path reports/tmp/video-demo-replay.json
```

Во время выполнения показать рядом терминал и браузер. В терминале обратить внимание на:

- текущую задачу на русском;
- выбранные инструменты, например `browser.navigate`, `browser.click_by_intent`, `browser.resolve_target`;
- очищенные аргументы инструментов;
- строки вида `Контекст сжат: X -> Y токенов`;
- `Решение безопасности`;
- остановку перед кнопкой `Apply`.

В браузере показать:

- локальный сайт на `127.0.0.1`;
- переход от главной страницы к результатам;
- чтение трех detail-страниц;
- отсутствие отправки отклика.

Когда появится запрос подтверждения перед `Apply`, ввести:

```text
нет
```

Сказать: "Агент дошел до внешнего действия и остановился. Это не ошибка демо, это проверяемая граница безопасности".

### 3:40-4:20 - replay/report

Показать безопасную сводку replay:

```powershell
scout-pilot replay-summary reports/tmp/video-demo-replay.json
```

Что отметить:

- видны страницы, наблюдения, tool calls, security pauses и context metrics;
- replay не содержит raw HTML, cookies, tokens, profile data и приватные файлы;
- JSON остается источником данных, а summary нужен только для удобного безопасного просмотра.

### 4:20-5:00 - честный HH.ru smoke

Показать команду, но не заявлять, что она всегда пройдет:

```powershell
scout-pilot run "Найди три подходящие AI Engineer или Python AI Developer вакансии, прочитай описания, сравни требования и остановись перед откликом." `
  --live `
  --provider openai `
  --start-url https://hh.ru `
  --headed `
  --dashboard verbose `
  --max-iterations 8 `
  --report-path reports/tmp/hh-smoke-report.json `
  --replay-path reports/tmp/hh-smoke-replay.json
```

Сказать:

- HH.ru не является зашитым workflow;
- команда стартует с URL, который дал пользователь;
- live provider требует локальный `.env`;
- CAPTCHA, login wall или региональный prompt считаются честной причиной остановки;
- отклики, сообщения и отправка форм не выполняются без явного подтверждения.

## Если нужен именно `run --live` на локальном сайте

`live-local-demo` удобнее для видео, потому что сам поднимает локальный сайт и включает deterministic mock-поведение для полного сценария. Если интервьюер просит показать низкоуровневую форму `scout-pilot run --live --start-url`, запустите локальный сайт отдельно.

Терминал 1:

```powershell
python -c "from pathlib import Path; from scout_pilot.demo.live_local import prepare_live_local_demo_site; prepare_live_local_demo_site(Path('reports/tmp/video-demo-site'))"
python -m http.server 8765 --bind 127.0.0.1 --directory reports/tmp/video-demo-site
```

Терминал 2:

```powershell
scout-pilot run "Найди три подходящие AI Engineer вакансии, прочитай описания и остановись перед откликом." `
  --live `
  --provider mock `
  --start-url http://127.0.0.1:8765/index.html `
  --headed `
  --dashboard verbose `
  --max-iterations 3 `
  --report-path reports/tmp/video-run-report.json `
  --replay-path reports/tmp/video-run-replay.json
```

Важно: `--provider mock` в обычном `run` выполняет короткую детерминированную проверку runtime, а не реальный LLM. Для полного локального сценария с чтением трех страниц и security pause используйте `live-local-demo`. Для реального reasoning используйте `--provider openai` или `--provider anthropic` после `provider-smoke`.

## Что не утверждать в видео

- Не говорить, что проект production-ready.
- Не говорить, что live HH.ru точно пройдет: сайт может показать CAPTCHA, логин или региональный экран.
- Не говорить, что автоматические тесты вызывают OpenAI/Anthropic.
- Не называть `mock` реальным LLM.
- Не утверждать, что агент отправляет отклики, письма, заказы или платежи. Он должен останавливаться перед такими действиями.
- Не показывать `.env`, cookies, browser profile, токены и приватные файлы.
- Не показывать raw JSON целиком, если там есть локальные пути или лишний debug context; лучше использовать `replay-summary`.

## Если что-то пошло не так

- `doctor` предупреждает об отсутствии `.env`: это нормально для mock demo.
- Браузер не открылся: запустите `python -m playwright install chromium` и повторите `scout-pilot doctor`.
- Live provider вернул ошибку ключа или rate limit: покажите `provider-smoke`, исправьте `.env` или переключитесь на `--provider mock`.
- HH.ru остановил CAPTCHA/login wall: скажите, что это ожидаемый blocker, и покажите локальный deterministic demo.
- Security prompt ждет ввод: введите `нет`, чтобы завершить демонстрацию безопасно.

## Финальная проверка перед записью

```powershell
python -m pytest -q
python -m ruff check .
scout-pilot live-local-demo --headless --slow-mo-ms 0 --dashboard off
scout-pilot replay-summary reports/tmp/live-local-demo-replay.json
```

Эти команды подтверждают тесты, базовый lint, headless-демо и читаемый безопасный replay.
