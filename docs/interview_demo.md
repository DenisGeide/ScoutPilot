# Interview demo

Основной локальный показ для интервью — `scout-pilot live-local-demo`. Он поднимает синтетический сайт на `127.0.0.1`, запускает обычный autonomous runtime и использует mock provider, поэтому не требует API-ключей, аккаунтов и живого HH.ru.

Старый `interview-demo` оставлен как scripted fallback. Его удобно держать под рукой, но для видео лучше начинать с `live-local-demo`, потому что там виден цикл observe-think-plan-act-evaluate.

## Что показывает `live-local-demo`

- видимый браузер через Browser Engine;
- отдельный persistent profile в `.browser-profiles/live-local-demo`;
- семантические наблюдения без raw HTML;
- выбор tools через Tool Runtime;
- ambiguous `Details` links и безопасное `browser.resolve_target`;
- переходы на три detail-страницы по обнаруженным URL;
- context budgeting, memory и reflection events в runtime report;
- остановку Security Policy перед кнопкой `Apply`;
- JSON report/replay без cookies, tokens, browser profile data, raw HTML и чувствительных значений.

## Локальный запуск

Для записи видео:

```powershell
scout-pilot live-local-demo --headed --slow-mo-ms 120 --dashboard compact
```

Для быстрой проверки без окна:

```powershell
scout-pilot live-local-demo --headless --slow-mo-ms 0 --dashboard off
```

По умолчанию артефакты пишутся в ignored-папку:

- `reports/tmp/live-local-demo-report.json`;
- `reports/tmp/live-local-demo-replay.json`;
- `reports/tmp/live-local-demo-site/`;
- `.browser-profiles/live-local-demo/`.

## Чек-лист для короткого видео

1. Покажите чистый статус репозитория:

```powershell
git status --short
```

2. Покажите, что проект установлен и CLI доступен:

```powershell
scout-pilot status
```

3. Запустите runtime demo:

```powershell
scout-pilot live-local-demo --headed --slow-mo-ms 120 --dashboard compact
```

4. Во время записи отметьте:

- браузер открывает локальный сайт на `127.0.0.1`;
- терминал показывает текущую задачу, состояние runtime, выбранный tool и очищенные аргументы;
- агент открывает результаты через семантические наблюдения, а не через CSS selectors или XPath;
- несколько ссылок `Details` сначала дают ambiguity check;
- после чтения трех detail-страниц действие `Apply` не выполняется, а останавливается на Security Policy;
- progress и confirmation output остаются на русском.

5. После завершения покажите короткий фрагмент отчета:

```powershell
Get-Content reports/tmp/live-local-demo-report.json -Encoding UTF8 | Select-Object -First 80
```

В отчете должны быть runtime events, selected tools, context budget metrics, security pause и итоговая сводка. Полного HTML, cookies, tokens и browser profile data там быть не должно.

## Scripted fallback

Если нужно быстро показать старый deterministic flow без runtime-провайдера:

```powershell
scout-pilot interview-demo --headed --slow-mo-ms 120
```

Для headless-проверки:

```powershell
scout-pilot interview-demo --headless --slow-mo-ms 0 --wait-after-search-ms 50
```

## Ручной live HH.ru smoke

Live HH.ru smoke отделен от локального demo и не входит в автоматические тесты:

```powershell
scout-pilot demo-vacancy-search `
  --start-url https://hh.ru `
  --query "AI Engineer Python AI Developer" `
  --max-vacancies 3 `
  --headed `
  --confirm-search-fill `
  --report-path reports/tmp/hh-demo-report.json `
  --replay-path reports/tmp/hh-demo-replay.json
```

Если сайт требует подтверждения запуска поиска, повторите с `--confirm-search-submit`. Не подтверждайте отклики, сообщения, загрузку файлов или отправку заявок для демонстрации.

Live HH.ru может показать CAPTCHA, вход или выбор региона. Это нормальная причина остановки; не подменяйте ее заранее подготовленной удачной записью.
