# Interview demo

Этот сценарий нужен для короткого видео или live-показа на интервью. Он показывает общий Browser Agent на локальных тестовых страницах, без аккаунтов, реальных вакансий, live LLM-вызовов и отправки данных.

## Что показывает локальное demo

- запуск видимого браузера через Browser Engine;
- отдельный persistent profile в `.browser-profiles/interview-demo`;
- семантические наблюдения страницы без raw HTML;
- выбор инструментов через Tool Runtime;
- семантическую навигацию без CSS selectors, XPath и маршрутов сайта;
- метрики бюджета контекста после наблюдений;
- остановку безопасности перед кнопкой `Apply`;
- итоговые заметки по трем найденным страницам;
- JSON report и replay без cookies, tokens, browser profile data и значений чувствительных полей.

## Локальный запуск

Для записи видео лучше использовать видимый браузер:

```powershell
scout-pilot interview-demo --headed --slow-mo-ms 120
```

Для быстрой детерминированной проверки без окна:

```powershell
scout-pilot interview-demo --headless --slow-mo-ms 0 --wait-after-search-ms 50
```

По умолчанию артефакты пишутся в ignored-папку:

- `reports/tmp/interview-demo-report.json`;
- `reports/tmp/interview-demo-replay.json`;
- `reports/tmp/interview-demo-site/`;
- `.browser-profiles/interview-demo/`.

## Чек-лист для короткого видео

1. Покажите чистый статус репозитория:

```powershell
git status --short
```

2. Покажите, что проект установлен и CLI доступен:

```powershell
scout-pilot status
```

3. Запустите локальное demo:

```powershell
scout-pilot interview-demo --headed --slow-mo-ms 120
```

4. Во время записи отметьте:

- браузер открывает локальный тестовый сайт на `127.0.0.1`;
- запрос вводится в поле поиска через семантическое описание;
- результаты открываются по обнаруженным ссылкам;
- отклик через `Apply` не выполняется, а останавливается на Security Policy;
- CLI пишет прогресс на русском.

5. После завершения покажите короткий фрагмент отчета:

```powershell
Get-Content reports/tmp/interview-demo-report.json -Encoding UTF8 | Select-Object -First 80
```

В отчете должны быть `summary`, `observation`, `selected_tool`, `context_budget`, `security_pause` и итоговая русская сводка. Полного HTML, cookies, tokens и browser profile data там быть не должно.

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
