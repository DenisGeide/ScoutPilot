# Архитектура

Scout Pilot строится как набор независимых слоев. На этапе фундамента слои представлены протоколами и доменными моделями, без конкретной браузерной или LLM-реализации.

## Слои

| Слой | Пакет | Ответственность |
|---|---|---|
| Browser Engine | `scout_pilot.browser` | Управляет видимым браузером, сессиями, навигацией и диагностическими скриншотами. Playwright изолирован здесь. |
| Semantic Observation Engine | `scout_pilot.observation` | Преобразует контролируемый Browser Engine snapshot в компактное семантическое наблюдение без полного HTML и значений чувствительных полей. |
| Tool Runtime | `scout_pilot.tools` | Регистрирует, валидирует и выполняет инструменты через provider-neutral схемы, ведет history и structured logs. |
| LLM Provider Layer | `scout_pilot.llm` | Изолирует OpenAI и Anthropic за единым интерфейсом. |
| Planning Engine | `scout_pilot.planning` | Строит и обновляет план выполнения пользовательской задачи. |
| Hierarchical Memory | `scout_pilot.memory` | Хранит рабочую, задачную и эпизодическую память с учетом приватности. |
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
- Провайдеры LLM не должны импортироваться в планировщик, память, безопасность или CLI напрямую.
- Документация и пользовательские сообщения остаются на русском; код, идентификаторы и внутренние логи — на английском.

## Будущие этапы

1. LLM Provider Layer подключит OpenAI или Anthropic через единый интерфейс и адаптирует нейтральные tool schemas.
2. Planning Engine начнет формировать шаги поверх observation и tools.
3. Memory, Context и Intelligence добавят восстановление, сжатие и оценку прогресса.
4. Security Policy Layer подключится к pre-execution hook перед чувствительными действиями.
5. CLI, reports и replay дадут демонстрационный режим и проверяемые пользовательские артефакты.
