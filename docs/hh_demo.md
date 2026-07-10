# Ручной smoke-тест HH.ru

Этот сценарий проверяет, что общий Browser Agent может пройти реалистичный поиск вакансий на HH.ru без hardcoded маршрутов, CSS selectors, XPath или workflow под конкретный сайт.

Автоматические тесты не ходят на HH.ru. Для них используются локальные тестовые страницы из `tests/test_demo_vacancy_search.py`, а для записи короткого видео удобнее начать с локального [interview demo](interview_demo.md).

## Перед запуском

1. Установите зависимости и Playwright:

```powershell
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

2. Убедитесь, что временные отчеты и профиль браузера не попадут в Git:

```powershell
git status --short
```

3. Запускайте live smoke в видимом браузере, чтобы видеть CAPTCHA, региональные окна, вход в аккаунт или другие блокирующие экраны.

## Базовый запуск

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

Команда должна:

- открыть URL, переданный пользователем;
- найти поле поиска через semantic observation;
- ввести поисковый запрос только после явного флага `--confirm-search-fill`;
- открыть до трех найденных страниц по обнаруженным ссылкам;
- подготовить короткие заметки;
- остановиться до отклика, сообщения или отправки формы;
- сохранить JSON-отчет без полного HTML и значений чувствительных полей.

## Если поиск требует подтверждения

Некоторые сайты запускают поиск через submit-кнопку. Если CLI остановился на подтверждении запуска поиска, прочитайте сообщение безопасности и повторите команду с дополнительным флагом:

```powershell
scout-pilot demo-vacancy-search `
  --start-url https://hh.ru `
  --query "AI Engineer Python AI Developer" `
  --max-vacancies 3 `
  --headed `
  --confirm-search-fill `
  --confirm-search-submit `
  --report-path reports/tmp/hh-demo-report.json `
  --replay-path reports/tmp/hh-demo-replay.json
```

`--confirm-search-submit` разрешает только запуск поиска. Он не подтверждает отклики, сообщения, отправку заявок или загрузку файлов.

## Проверка Security Policy

Для локальной проверки демо можно запустить:

```powershell
python -m pytest tests/test_demo_vacancy_search.py
```

Тест проверяет, что кнопка `Apply` останавливается на deterministic security confirmation и не выполняется.

На живом HH.ru флаг `--probe-security` используйте только вручную и внимательно: агент должен записать security pause и не продолжать автоматически.

## Честные ограничения

Live HH.ru может показать CAPTCHA, страницу входа, выбор региона, A/B-разметку или временно изменить интерфейс. Это нормальная причина для остановки или неполного отчета. Демо не должно создавать выдуманные вакансии, скриншоты или доказательства успеха.

Если отчет содержит `captcha_blocking_page`, `login_wall`, `region_prompt`, `cookie_banner`, `blocked_page`, `empty_page`, `no_candidates` или `confirmation_required`, это рабочий результат проверки безопасности и наблюдаемости, а не повод обходить политику.
