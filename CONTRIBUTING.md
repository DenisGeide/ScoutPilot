# Как вносить изменения

Спасибо, что смотрите проект. Это учебный репозиторий, поэтому главная цель изменений — сохранить код понятным и проверяемым, а не добавить как можно больше возможностей.

## Перед началом

```powershell
python -m pip install -e ".[dev]"
python -m playwright install chromium
python -m pytest
```

## Основные правила

- Код, имена классов, функций, модулей и внутренние structured logs пишутся на английском.
- README, пользовательская документация и CLI-сообщения пишутся на русском.
- Playwright импортируется только внутри `scout_pilot.browser`.
- OpenAI/Anthropic SDK imports остаются внутри `scout_pilot.llm`.
- LLM не получает полный HTML, DOM dumps, cookies, tokens, browser profiles или приватные файлы.
- Browser actions проходят через Tool Runtime и Security Policy.
- Confirmation-required actions не продолжаются автоматически.
- Автоматические тесты должны быть детерминированными: без live HH.ru и без live LLM API.

## Что проверять перед коммитом

```powershell
python -m pytest
python -m compileall -q src tests
scout-pilot status
scout-pilot browser-smoke --headless --hold-seconds 0
git diff --check
git status --short
```

Для изменений в документации достаточно убедиться, что команды в README и `docs/` совпадают с реальным CLI.

## Безопасность артефактов

Не добавляйте в Git:

- `.env`;
- API keys, tokens, cookies;
- browser profiles и session state;
- приватные скриншоты;
- реальные резюме или личные файлы;
- временные отчеты из `reports/tmp/`;
- кеши Python, pytest и Playwright.

Если изменение касается report/replay, проверьте, что sanitizer по-прежнему удаляет raw HTML, приватные пути и чувствительные поля.

## Документация

Если меняется поведение, обновите ближайший документ:

- установка и конфигурация: [docs/setup.md](docs/setup.md);
- архитектурные границы: [docs/architecture.md](docs/architecture.md);
- тестирование: [docs/testing.md](docs/testing.md);
- interview demo: [docs/interview_demo.md](docs/interview_demo.md);
- ручной HH.ru smoke: [docs/hh_demo.md](docs/hh_demo.md);
- правила разработки: [docs/development.md](docs/development.md).
