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
| Autonomous Agent Runtime | `scout_pilot.runtime` | Координирует цикл агента, состояния и события выполнения. |
| Execution Intelligence | `scout_pilot.intelligence` | Оценивает прогресс, причины неудач и необходимость повторных попыток. |
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
- Документация и пользовательские сообщения остаются на русском; код, идентификаторы и внутренние логи — на английском.

## Будущие этапы

1. Autonomous Agent Runtime начнет использовать Planning Engine, Reasoning Engine, Hierarchical Memory и Tool Runtime в едином цикле.
2. Context и Intelligence добавят восстановление, сжатие и оценку прогресса поверх bounded memory summaries.
3. Security Policy Layer подключится к pre-execution hook перед чувствительными действиями.
4. CLI, reports и replay дадут демонстрационный режим и проверяемые пользовательские артефакты.
