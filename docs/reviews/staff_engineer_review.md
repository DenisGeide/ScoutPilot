# Staff Engineer review

Дата: 2026-07-10.

## Findings fixed

- Заблокирована навигация `file://` через Tool Runtime. До исправления `browser.navigate` считался безопасным для всех URL, а Browser Engine поддерживал local files для тестов. Это создавало риск, что LLM-предложение откроет локальный приватный файл и его содержимое попадет в semantic observation или model context.
- Детерминированные tests, которые проверяли generic demo/navigation через Tool Runtime, переведены с `file://` на локальный HTTP-сервер `127.0.0.1`. Низкоуровневые Browser Engine tests по-прежнему могут открывать local pages напрямую.
- Документация архитектуры дополнена правилом: `file://` navigation блокируется Security Policy на уровне Tool Runtime.

## Findings intentionally deferred

- `browser.screenshot` остается разрешенным как диагностический tool. Это локальный артефакт, а не внешний side effect; путь для скриншотов находится в ignored `reports/tmp/`. Дополнительное подтверждение для screenshot можно добавить позже, если demo начнет работать с приватными аккаунтами.
- Live HH.ru smoke остается ручным и непригодным для CI. Это соответствует текущей testing policy, потому что сайт может показать CAPTCHA, вход или A/B-разметку.

## Residual risks

- Browser Engine напрямую поддерживает `file://` для локальных integration tests. Это безопасно, пока autonomous/runtime/tool paths используют Tool Runtime и Security Policy.
- Отчеты зависят от sanitizer. Тесты покрывают raw HTML, token/cookie markers и private path-like values, но при добавлении новых report fields нужно продолжать писать regression tests.
- Live provider calls не проверяются автоматическими тестами. Provider adapters покрыты mocks; реальные ключи и сеть намеренно не требуются для deterministic validation.

## Tests run

- `python -m pytest -q`
- `python -m compileall -q src tests`
- import sweep for all `scout_pilot` modules
- `git diff --check`
- `scout-pilot status`
- `scout-pilot browser-smoke --headless --hold-seconds 0`
- `scout-pilot interview-demo --headless --slow-mo-ms 0 --wait-after-search-ms 50`
- boundary scans for Playwright/provider imports, complete HTML APIs and demo-specific routes/selectors
