# Заметки для разработки

Этот проект развивается по prompt pack из папки `promts/codex_pack_final`. Каждый этап должен быть небольшим, проверяемым и честно отраженным в документации.

## Принципы

- Держать прямой импорт Playwright только внутри `scout_pilot.browser`.
- Не использовать полные HTML-страницы или DOM-дампы для наблюдений.
- Не включать значения полей ввода в LLM-facing observation.
- Не добавлять provider-specific schema conversion в Tool Runtime.
- Проверять tool inputs до обращения к Browser Engine.
- Держать OpenAI/Anthropic SDK imports только внутри `scout_pilot.llm`.
- Использовать mocked providers в автоматических тестах LLM layer.
- Не вызывать реальные LLM API в детерминированных тестах.
- Планировщик должен ссылаться на semantic tool capabilities, а не на CSS selectors, XPath, Playwright locators или hardcoded routes.
- Replanning обязан сохранять уже выполненные шаги плана.
- Память делится на working, task и episodic layers; не добавлять один общий неструктурированный memory blob.
- В memory можно хранить цель пользователя, ограничения, подтвержденные выборы, warnings и компактные event summaries.
- В memory нельзя хранить secrets, cookies, tokens, session state, browser profiles, полный HTML/DOM, приватные screenshots, приватные файлы и значения чувствительных полей.
- Для LLM/context использовать только bounded memory summaries.
- Runtime не должен импортировать Playwright или provider SDKs; он вызывает Observation Engine, Planning Engine, Reasoning Engine, Tool Runtime и Memory через их интерфейсы.
- Любое завершение runtime должно иметь явный `AgentTaskResult` и понятный `termination_reason`.
- State transitions должны иметь reason и внутренний structured log на английском.
- Runtime tests должны использовать mocked providers, fake tools и synthetic observations, без live LLM и live сайтов.
- Execution Intelligence должен оставаться deterministic и website-neutral: никаких CSS selectors, XPath, provider calls или прямого доступа к Playwright.
- Reflection summaries должны быть короткими, безопасными для memory и не содержать raw HTML, cookies, tokens, field values или приватные файлы.
- Replanning вызывается через Planning Engine; evaluator только рекомендует retry/replan/observe/confirm/stop и не исполняет tools.
- Любой model-facing payload для Reasoning Engine или Planning Engine должен проходить через `DeterministicContextBudgeter`.
- Context Budgeting должен сохранять user goal, constraints, confirmation choices, security warnings и recent failures, а repeated navigation/header/footer, stale observations и boilerplate отбрасывать первыми.
- Метрики `before_tokens`/`after_tokens` и признаки emergency compression нужны для runtime/debug events; raw HTML или DOM dumps нельзя отправлять в LLM ради "лучшего" сжатия.
- Security Policy должен выполняться перед каждым `tool.execute()` и не должен доверять LLM-provided `risk`, prompt-инструкциям или аргументам вроде `safe=true`.
- Confirmation-required action возвращает paused result и русское сообщение; runtime не продолжает автоматически, а разрешает только один exact request после явного подтверждения.
- Security audit trail хранит классификацию, outcome и confirmation id, но не должен раскрывать значения чувствительных полей.
- Не использовать live HH.ru в автоматических тестах.
- Не хранить секреты, cookies, session state, приватные скриншоты и временные отчеты в репозитории.
- Добавлять тесты пропорционально риску изменения.
- Сохранять английский язык в коде и русский язык в пользовательском интерфейсе.

## Типовой локальный цикл

```powershell
python -m pip install -e ".[dev]"
python -m playwright install chromium
python -m pytest
scout-pilot status
```

Локальная проверка Browser Engine:

```powershell
scout-pilot browser-smoke --headed --hold-seconds 5
```

## Что считается готовым этапом

- Scope этапа реализован без лишних возможностей.
- Тесты и базовые команды проходят или причина отсутствия проверки явно описана.
- Документация обновлена, если изменилось поведение или структура.
- Следующий prompt можно запускать без догадок о текущем состоянии.
