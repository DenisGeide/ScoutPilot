# Тестирование

Автоматические тесты в проекте должны быть детерминированными. Они не ходят на HH.ru, не вызывают OpenAI или Anthropic и не зависят от внешних страниц.

## Основная проверка

```powershell
python -m pytest
```

Ожидаемый результат на текущем этапе: весь набор тестов проходит локально. Точное число тестов может меняться по мере развития проекта, поэтому в документации не фиксируется как обещание.

## Что покрывают тесты

- Browser Engine на локальных тестовых страницах.
- Semantic Observation Engine без полного HTML в model-facing структурах.
- Tool Runtime: validation, timeout, browser failure, security pause, logging.
- LLM Provider Layer через mocked providers и fake clients.
- Planning Engine и Reasoning Engine без live LLM API.
- Hierarchical Memory, Context Budgeting и privacy filtering.
- Autonomous Agent Runtime, cancellation, retry/failure limits и confirmation pause.
- Execution Intelligence: no-op, repeated failures, stale elements, replanning.
- Universal Semantic Navigation на разных локальных тестовых сайтах.
- HH.ru-like vacancy flow на локальных тестовых страницах.
- CLI parsing, dry-run/live report/replay, snapshot-style dashboard messages и sanitizer.

## Локальные smoke-проверки

CLI:

```powershell
scout-pilot status
scout-pilot run "Проверить страницу" --dry-run --dashboard off
scout-pilot run "Проверить страницу" --live --provider mock --start-url https://example.com --headless --max-iterations 3 --dashboard off
```

Ручная проверка OpenAI/Anthropic вынесена в отдельную команду и не входит в CI:

```powershell
scout-pilot provider-smoke --provider openai
```

Если ключа в локальном `.env` нет, команда завершается с ненулевым кодом и понятным русским сообщением. В автоматических тестах это проверяется без live API-вызова.

Browser Engine:

```powershell
scout-pilot browser-smoke --headless --hold-seconds 0
scout-pilot profile-open --profile default --start-url https://example.com --headless --hold-seconds 0
```

Runtime demo без живого сайта:

```powershell
scout-pilot live-local-demo --headless --slow-mo-ms 0 --dashboard off
```

Scripted interview demo без живого сайта:

```powershell
scout-pilot interview-demo --headless --slow-mo-ms 0 --wait-after-search-ms 50
```

Демо без живого сайта:

```powershell
python -m pytest tests/test_demo_vacancy_search.py
```

## Проверки границ

Перед коммитом полезно убедиться, что архитектурные границы не сломаны:

```powershell
rg -n "from playwright|import playwright" src\scout_pilot | rg -v "src\\scout_pilot\\browser"
rg -n "from openai|import openai|from anthropic|import anthropic" src\scout_pilot | rg -v "src\\scout_pilot\\llm"
rg -n "page\.content\(|inner_html|outer_html|innerHTML|outerHTML" src\scout_pilot
```

Для этих команд отсутствие вывода означает, что нарушений не найдено.

## Live HH.ru не входит в CI

Ручной HH.ru smoke нужен, чтобы увидеть поведение на реальном сайте, но он не является автоматическим тестом. Причины:

- сайт может показать CAPTCHA, вход или выбор региона;
- интерфейс может измениться без предупреждения;
- live-тест не должен отправлять отклики, сообщения или формы;
- результаты нельзя подделывать, если сайт заблокировал сценарий.

Для live-проверки используйте [hh_demo.md](hh_demo.md).
