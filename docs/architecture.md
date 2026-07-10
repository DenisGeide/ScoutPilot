# Архитектура

Scout Pilot строится как набор независимых слоев. Реализованные слои имеют конкретные адаптеры или bounded in-memory реализации; будущие слои пока представлены протоколами и доменными моделями.

## Слои

| Слой | Пакет | Ответственность |
|---|---|---|
| Browser Engine | `scout_pilot.browser` | Управляет видимым браузером, сессиями, навигацией и диагностическими скриншотами. Playwright изолирован здесь. |
| Semantic Observation Engine | `scout_pilot.observation` | Преобразует контролируемый Browser Engine snapshot в компактное семантическое наблюдение без полного HTML и значений чувствительных полей. |
| Universal Semantic Navigation | `scout_pilot.navigation` | Разрешает website-neutral navigation intents по semantic observation IDs, выбирает ссылки/кнопки/поля по roles, names и visible context, обнаруживает search fields, строит form-fill plan и помогает восстановиться после stale IDs. |
| Tool Runtime | `scout_pilot.tools` | Регистрирует, валидирует и выполняет инструменты через provider-neutral схемы, ведет history и structured logs. |
| LLM Provider Layer | `scout_pilot.llm` | Изолирует OpenAI и Anthropic за единым интерфейсом, содержит provider-specific tool schema adapters и Reasoning Engine. |
| Planning Engine | `scout_pilot.planning` | Строит и обновляет короткий provider-neutral план по user goal, semantic observation, memory summaries и available tool schemas, не исполняя tools. |
| Hierarchical Memory | `scout_pilot.memory` | Хранит bounded working, task и episodic memory, фильтрует приватные данные и отдает compact summaries для planner/reasoning/context. |
| Autonomous Agent Runtime | `scout_pilot.runtime` | Координирует observe-think-plan-act-evaluate loop, state machine, memory, tool execution, progress, cancellation и explicit termination. |
| Execution Intelligence | `scout_pilot.intelligence` | Оценивает tool outcomes, прогресс, no-op действия, повторные ошибки, валидность плана и необходимость retry/replan/confirmation/stop. |
| Context Budgeting and Compression | `scout_pilot.context` | Оценивает model input size, резервирует output tokens, сжимает observations/memory, удаляет повторяющийся boilerplate и отдает прозрачные before/after metrics для runtime/debug. |
| Independent Security Policy Layer | `scout_pilot.security` | Детерминированно классифицирует tool requests как `safe`, `sensitive`, `destructive` или `external_side_effect`, требует подтверждение на русском и ведет audit trail. |
| CLI/user interface | `scout_pilot.cli` | Показывает пользователю прогресс, предупреждения, ошибки и подтверждения на русском. |
| Reporting and replay | `scout_pilot.reporting` | Формирует HTML-free JSON-отчеты, фиксирует безопасные replay events, выбранные tools, security pauses и итоговые заметки. |
| Demonstrations | `scout_pilot.demo` | Собирает end-to-end сценарии поверх общих слоев без per-site selectors, hardcoded internal routes или прямого доступа к Playwright. |

## Правила границ

- Реализация Playwright не должна выходить за пределы Browser Engine.
- LLM не получает полный HTML, полный DOM или сырые Playwright-объекты.
- Semantic Observation Engine работает только с sanitized Browser Engine snapshots.
- Universal Semantic Navigation работает только с `PageObservation` и provider-neutral tool contracts: он не знает Playwright, CSS selectors, XPath, DOM handles, hardcoded URLs или website-specific workflows.
- Generic semantic tools (`browser.resolve_target`, `browser.click_by_intent`, `browser.fill_by_label`, `browser.plan_form_fill`) сначала разрешают намерение через observation IDs; неоднозначные цели возвращают structured failure вместо опасного угадывания.
- Tool Runtime запускает deterministic Security Policy перед `tool.execute()` и не содержит provider-specific schema adapters.
- LLM не может пометить действие как безопасное: `ToolRequest.risk` и аргументы модели не используются как источник доверия для security decision.
- Sensitive, destructive и external-side-effect actions возвращают paused result с confirmation request; выполнение продолжается только после явного подтверждения exact request.
- Провайдеры LLM и SDK imports не должны выходить за пределы `scout_pilot.llm`.
- Reasoning Engine получает только user task, compact observation, memory summaries, tool schemas, constraints и budget.
- Все model-facing запросы в Reasoning Engine и Planning Engine проходят через `DeterministicContextBudgeter`; raw HTML, DOM dumps, cookies, tokens, browser profiles и private files не попадают в payload.
- Context Budgeting сначала удаляет repeated navigation/header/footer content и stale observations, затем сжимает oversized sections, а при нехватке бюджета включает emergency compression.
- Context Budgeting обязан сохранять user instructions, confirmation decisions, security warnings, task constraints и recent failures, даже когда low-value memory summaries отбрасываются.
- Planning Engine получает только compact observation и нейтральные tool schemas; план не должен содержать CSS selectors, XPath, Playwright locators или hardcoded route paths.
- Planning Engine может помечать шаги как uncertain или requires_confirmation, но не выполняет browser actions.
- Hierarchical Memory не является глобальным blob: working memory ограничена текущим циклом, task memory хранит важные факты задачи, episodic memory хранит компактную историю событий.
- Memory не хранит secrets, cookies, tokens, полные HTML/DOM, session state, browser profiles, приватные screenshots, приватные файлы и значения чувствительных полей.
- Memory отделена от logs: в нее попадают только отфильтрованные записи, полезные для будущего reasoning context.
- Autonomous Agent Runtime не импортирует Playwright или provider SDKs; browser actions проходят только через Tool Runtime, reasoning — только через provider-neutral Reasoning Engine.
- Runtime state transitions всегда имеют machine-readable reason и пишутся во внутренний structured log на английском.
- Runtime events содержат `message_key`, чтобы пользовательский интерфейс мог локализовать progress и ошибки на русский.
- Runtime не продолжает автоматически после confirmation-required action: подтверждение только разрешает один следующий exact tool request.
- Stale semantic IDs восстанавливаются через повторное observation и remap кандидата по тому же website-neutral intent; старые DOM handles не сохраняются.
- Демонстрационные сценарии используют только URL, переданный пользователем, и URL, обнаруженные из текущего semantic observation.
- HH.ru допускается как live smoke target в документации, но не как source-code workflow: в `scout_pilot.demo` не должно быть HH.ru routes, CSS selectors, XPath или assumptions о внутренних путях сайта.
- Demo reports включают компактные observations, tool decisions, security pauses и short notes; полный HTML, DOM dumps, cookies, tokens, profile data и значения чувствительных полей туда не попадают.
- Execution Intelligence получает только compact observations, provider-neutral tool results и plan state; он не обращается к Playwright, provider SDKs, raw HTML, cookies или browser profiles.
- Reflection summaries сохраняются в memory как компактные episodic summaries, а не как raw traces.
- Документация и пользовательские сообщения остаются на русском; код, идентификаторы и внутренние логи — на английском.

## Runtime lifecycle

Autonomous Agent Runtime выполняет задачу как bounded loop:

1. Создает task scope и сохраняет user goal в task memory.
2. Переходит в `observing` и получает compact semantic observation.
3. При первом проходе переходит в `planning` и строит execution plan.
4. Переходит в `reasoning` и запрашивает provider-neutral решение у Reasoning Engine.
5. Если выбран tool, переходит в `executing` и вызывает только Tool Runtime.
6. После каждого tool execution переходит в `evaluating`, получает post-action observation и вызывает Execution Intelligence.
7. Reflection классифицирует outcome как `success`, `failure` или `uncertain` и рекомендует `continue`, `observe_again`, `retry`, `replan`, `request_confirmation` или `stop`.
8. Завершает задачу только явным result: `completed`, `waiting_for_confirmation`, `cancelled` или `failed`.

Защиты runtime:

- `max_iterations` останавливает бесконечные циклы наблюдений и рассуждений.
- `max_failures` ограничивает повторные ошибки provider/tool/recovery.
- `cancel()` завершает задачу через `cancelled` без дополнительных browser actions.
- Retryable tool failures переводят runtime в `retrying` и могут вызвать `PlanningEngine.revise_plan`.
- Повторные no-op observations и повторяющиеся tool failures превращаются в reflection events и bounded memory summaries, чтобы runtime мог перестроить план без website-specific логики.

## Будущие этапы

1. Полноценный автономный CLI-режим может использовать уже готовые semantic tools, security confirmations и безопасный report/replay формат.
2. Live HH.ru smoke остается ручной проверкой: автоматические тесты продолжают опираться на synthetic pages и mocked providers.
