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
- Для новых браузерных сценариев сначала использовать semantic observation IDs и `scout_pilot.navigation`: `browser.resolve_target`, `browser.click_by_intent`, `browser.fill_by_label` и `browser.plan_form_fill`. Не добавлять per-site selectors, XPath, internal route assumptions или adapters под конкретный сайт.
- Если semantic target неоднозначен, tool должен вернуть structured failure и кандидатов, а не выбирать первый подходящий элемент.
- Stale element recovery должен пере-наблюдать страницу и remap-ить кандидата по semantic intent; нельзя хранить или переиспользовать DOM handles вне Browser Engine.
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
- Demo flows должны начинаться с URL, переданного пользователем, и работать через semantic observations/tool runtime. HH.ru можно использовать только как live smoke target, а не как hardcoded workflow.
- Demo reports должны хранить безопасные observations, выбранные tools, security pauses и short notes; не включать полный HTML, DOM dumps, cookies, tokens, profile data, приватные скриншоты и значения чувствительных полей.
- CLI `run` и `interactive` должны показывать русский progress/dashboard и писать report/replay только через sanitizer. Не добавлять raw logs, HTML, cookies, tokens, browser profiles, session data или чувствительные form values в пользовательские артефакты.
- Internal verbose/debug logs должны оставаться machine-readable English JSON-lines и не заменять русские пользовательские сообщения.
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

Проверка CLI dry-run и report/replay:

```powershell
scout-pilot run "Проверить страницу и подготовить краткий отчет" --dry-run
scout-pilot --verbose run "Проверить страницу" --dry-run --dashboard off
```

Проверка live CLI без внешних LLM-вызовов:

```powershell
scout-pilot run "Проверить страницу и подготовить краткий отчет" --live --provider mock --start-url https://example.com --headless --max-iterations 3 --dashboard off
```

Интерактивный режим:

```powershell
scout-pilot interactive --dry-run
```

Локальная проверка demo flow без живого сайта:

```powershell
python -m pytest tests/test_demo_vacancy_search.py
```

Ручной live smoke для HH.ru:

```powershell
scout-pilot demo-vacancy-search --start-url https://hh.ru --query "AI Engineer Python AI Developer" --max-vacancies 3 --headed --confirm-search-fill --report-path reports/tmp/hh-demo-report.json
```

Если CLI остановился на подтверждении запуска поиска, продолжайте только для самого поиска и только с явным флагом `--confirm-search-submit`. Отклики, сообщения и отправку заявок в демо не подтверждать.

## Операционные лимиты и отказы

- Браузерные действия должны завершаться structured result, а не необработанным исключением. Таймауты, HTTP 4xx/5xx, stale semantic IDs, закрытый браузер и неожиданные dialogs считаются нормальными failure paths.
- `PlaywrightBrowserEngine.stop()` должен пытаться закрыть и browser context, и Playwright runtime даже если первый шаг cleanup завершился ошибкой.
- Tool Runtime обязан остановиться до браузерного действия, если pre-execution hook или Security Policy падают. Такие сбои проверяются тестами и не считаются разрешением на действие.
- Provider failures в Reasoning Engine должны превращаться в `ReasoningResult.failure` с нормализованным кодом, чтобы runtime мог отдать понятное русское сообщение пользователю и не терял state machine.
- Ответ модели с неизвестным tool, пустым tool name, не-object arguments или оборванным по output token limit считается malformed и не передается дальше в Tool Runtime.
- Security pause остается обязательной: после confirmation-required result runtime не продолжает автоматически и разрешает только один exact request после явного подтверждения.
- Debug logs и report/replay должны помогать расследовать сбой, но не должны включать raw HTML, DOM dumps, cookies, tokens, browser profiles, session data, private paths или sensitive form values.

## Что считается готовым этапом

- Scope этапа реализован без лишних возможностей.
- Тесты и базовые команды проходят или причина отсутствия проверки явно описана.
- Документация обновлена, если изменилось поведение или структура.
- Следующий prompt можно запускать без догадок о текущем состоянии.
