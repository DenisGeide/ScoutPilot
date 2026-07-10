# Архитектура

Scout Pilot строится как набор независимых слоев. Реализованные слои имеют конкретные адаптеры или bounded in-memory реализации; будущие слои пока представлены протоколами и доменными моделями.

## Слои

| Слой | Пакет | Ответственность |
|---|---|---|
| Browser Engine | `scout_pilot.browser` | Управляет видимым браузером, сессиями, навигацией и диагностическими скриншотами. Playwright изолирован здесь. |
| Semantic Observation Engine | `scout_pilot.observation` | Преобразует контролируемый Browser Engine snapshot в компактное семантическое наблюдение без полного HTML и значений чувствительных полей. |
| Tool Runtime | `scout_pilot.tools` | Регистрирует, валидирует и выполняет инструменты через provider-neutral схемы, ведет history и structured logs. |
| LLM Provider Layer | `scout_pilot.llm` | Изолирует OpenAI и Anthropic за единым интерфейсом, содержит provider-specific tool schema adapters и Reasoning Engine. |
| Planning Engine | `scout_pilot.planning` | Строит и обновляет короткий provider-neutral план по user goal, semantic observation, memory summaries и available tool schemas, не исполняя tools. |
| Hierarchical Memory | `scout_pilot.memory` | Хранит bounded working, task и episodic memory, фильтрует приватные данные и отдает compact summaries для planner/reasoning/context. |
| Autonomous Agent Runtime | `scout_pilot.runtime` | Координирует observe-think-plan-act-evaluate loop, state machine, memory, tool execution, progress, cancellation и explicit termination. |
| Execution Intelligence | `scout_pilot.intelligence` | Оценивает tool outcomes, прогресс, no-op действия, повторные ошибки, валидность плана и необходимость retry/replan/confirmation/stop. |
| Context Budgeting and Compression | `scout_pilot.context` | Контролирует размер контекста и сжимает наблюдения. |
| Independent Security Policy Layer | `scout_pilot.security` | Классифицирует действия и требует подтверждение до внешних эффектов. |
| CLI/user interface | `scout_pilot.cli` | Показывает пользователю прогресс, предупреждения, ошибки и подтверждения на русском. |
| Reporting and replay | `scout_pilot.reporting` | Формирует отчеты и поддерживает безопасное воспроизведение сценариев. |

## Правила границ

- Реализация Playwright не должна выходить за пределы Browser Engine.
- LLM не получает полный HTML, полный DOM или сырые Playwright-объекты.
- Semantic Observation Engine работает только с sanitized Browser Engine snapshots.
- Tool Runtime имеет pre-execution hook для будущего Security Policy Layer и не содержит provider-specific schema adapters.
- Провайдеры LLM и SDK imports не должны выходить за пределы `scout_pilot.llm`.
- Reasoning Engine получает только user task, compact observation, memory summaries, tool schemas, constraints и budget.
- Planning Engine получает только compact observation и нейтральные tool schemas; план не должен содержать CSS selectors, XPath, Playwright locators или hardcoded route paths.
- Planning Engine может помечать шаги как uncertain или requires_confirmation, но не выполняет browser actions.
- Hierarchical Memory не является глобальным blob: working memory ограничена текущим циклом, task memory хранит важные факты задачи, episodic memory хранит компактную историю событий.
- Memory не хранит secrets, cookies, tokens, полные HTML/DOM, session state, browser profiles, приватные screenshots, приватные файлы и значения чувствительных полей.
- Memory отделена от logs: в нее попадают только отфильтрованные записи, полезные для будущего reasoning context.
- Autonomous Agent Runtime не импортирует Playwright или provider SDKs; browser actions проходят только через Tool Runtime, reasoning — только через provider-neutral Reasoning Engine.
- Runtime state transitions всегда имеют machine-readable reason и пишутся во внутренний structured log на английском.
- Runtime events содержат `message_key`, чтобы пользовательский интерфейс мог локализовать progress и ошибки на русский.
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

1. Context Budgeting усилит сжатие observation/memory перед Reasoning Engine.
2. Security Policy Layer подключится к pre-execution hook перед чувствительными действиями.
3. CLI, reports и replay дадут демонстрационный режим и проверяемые пользовательские артефакты.
