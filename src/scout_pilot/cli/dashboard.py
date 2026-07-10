"""Console status rendering for CLI runtime sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from scout_pilot.models import RuntimeEvent


@dataclass
class RuntimeDashboardState:
    """Current CLI-facing task status."""

    task: str = ""
    state: str = "ожидание"
    current_step: str = "ожидание задачи"
    selected_tool: str = "-"
    progress: str = "0/0"
    elapsed_seconds: float = 0.0
    next_action: str = "ожидание"


class RuntimeDashboard:
    """Render a compact Russian status view from runtime events."""

    def __init__(self, *, task: str, started_at: datetime | None = None) -> None:
        self._started_at = started_at or datetime.now(tz=timezone.utc)
        self.state = RuntimeDashboardState(task=task)

    def update(self, event: RuntimeEvent) -> RuntimeDashboardState:
        details = dict(event.details)
        self.state.elapsed_seconds = (
            datetime.now(tz=timezone.utc) - self._started_at
        ).total_seconds()
        self.state.state = _state_label(str(details.get("state") or self.state.state))
        self.state.current_step = _step_text(event)
        self.state.next_action = _action_label(
            str(details.get("next_action") or "")
        ) or _next_action(event)
        selected_tool = details.get("selected_tool") or details.get("tool_name")
        if selected_tool:
            self.state.selected_tool = _tool_label(str(selected_tool))
        progress = details.get("progress")
        if isinstance(progress, Mapping):
            self.state.progress = _progress_text(progress)
        if event.name == "task_started":
            task = details.get("task")
            if task:
                self.state.task = str(task)
        return self.state

    def render(self) -> str:
        return "\n".join(
            (
                f"Задача: {self.state.task}",
                f"Состояние: {self.state.state}",
                f"Текущий шаг: {self.state.current_step}",
                f"Выбранный инструмент: {self.state.selected_tool}",
                f"Прогресс: {self.state.progress}",
                f"Прошло: {self.state.elapsed_seconds:.1f} с",
                f"Следующее действие: {self.state.next_action}",
            )
        )

    def render_event(self, event: RuntimeEvent) -> str:
        self.update(event)
        return self.render()


def _progress_text(progress: Mapping[object, object]) -> str:
    completed = progress.get("completed_steps", 0)
    total = progress.get("total_steps", 0)
    iteration = progress.get("iteration", 0)
    max_iterations = progress.get("max_iterations", 0)
    if total:
        return f"{completed}/{total} шагов, итерация {iteration}/{max_iterations}"
    return f"итерация {iteration}/{max_iterations}"


def _step_text(event: RuntimeEvent) -> str:
    mapping = {
        "task_started": "задача принята",
        "plan_created": "план подготовлен",
        "observation_captured": "наблюдение подготовлено",
        "tool_selected": "инструмент выбран",
        "reasoning_completed": "решение подготовлено",
        "task_completed": "задача завершена",
        "task_failed": "задача остановлена с ошибкой",
    }
    if event.name == "state_transition":
        to_state = event.details.get("to_state")
        return _state_step(str(to_state or event.details.get("state") or ""))
    return mapping.get(event.name, "обновление состояния")


def _next_action(event: RuntimeEvent) -> str:
    mapping = {
        "task_started": "подготовить план выполнения",
        "plan_created": "проверить, какие данные нужны перед действиями",
        "observation_captured": "оценить доступный безопасный контекст",
        "tool_selected": "показать выбранное действие без выполнения",
        "reasoning_completed": "подготовить ответ или следующее действие",
        "task_completed": "прочитать итог и отчет",
        "task_failed": "прочитать причину и следующий шаг",
    }
    if event.name == "state_transition":
        return _state_next_action(str(event.details.get("to_state") or ""))
    return mapping.get(event.name, "продолжить выполнение")


def _state_step(state: str) -> str:
    return {
        "planning": "подготовка плана",
        "observing": "подготовка наблюдения",
        "reasoning": "оценка следующего действия",
        "executing": "выбор инструмента",
        "evaluating": "оценка результата",
        "waiting_for_confirmation": "ожидание подтверждения",
        "retrying": "повторная попытка",
        "completed": "завершение",
        "cancelled": "отмена",
        "failed": "остановка",
    }.get(state, _state_label(state))


def _state_next_action(state: str) -> str:
    return {
        "planning": "собрать короткий безопасный план",
        "observing": "подготовить безопасное наблюдение страницы",
        "reasoning": "проверить ограничения безопасности",
        "executing": "выполнить выбранный инструмент после проверки безопасности",
        "evaluating": "оценить, помогло ли действие задаче",
        "waiting_for_confirmation": "дождаться явного решения пользователя",
        "retrying": "повторить действие только если это безопасно",
        "completed": "сохранить отчет и replay",
        "cancelled": "завершить без дальнейших действий",
        "failed": "показать понятную причину",
    }.get(state, "продолжить")


def _state_label(state: str) -> str:
    return {
        "idle": "ожидание",
        "planning": "планирование",
        "observing": "наблюдение",
        "reasoning": "выбор следующего действия",
        "executing": "подготовка действия",
        "evaluating": "оценка результата",
        "waiting_for_confirmation": "ожидание подтверждения",
        "retrying": "повторная попытка",
        "completed": "завершено",
        "cancelled": "отменено",
        "failed": "остановлено",
    }.get(state, state if _looks_russian(state) else "обновление состояния")


def _action_label(action: str) -> str:
    if not action:
        return ""
    return {
        "prepare_plan": "подготовить план выполнения",
        "summarize_constraints": "учесть ограничения задачи",
        "prepare_observation": "подготовить наблюдение страницы",
        "prepare_placeholder_observation": "подготовить безопасное наблюдение без браузера",
        "evaluate_next_action": "оценить следующее безопасное действие",
        "select_tool": "выбрать подходящий инструмент",
        "skip_execution": "не выполнять действие в сухом запуске",
        "execute_tool": "выполнить инструмент после проверки безопасности",
        "prepare_summary": "подготовить итог",
        "write_report": "записать отчет и replay",
        "review_report": "проверить итог и отчет",
        "rerun with --dry-run": "повторить команду с --dry-run",
    }.get(action, action if _looks_russian(action) else "продолжить выполнение")


def _tool_label(tool_name: str) -> str:
    labels = {
        "browser.navigate": "открытие страницы",
        "browser.observe": "наблюдение страницы",
        "browser.click": "нажатие элемента",
        "browser.click_by_intent": "нажатие по смысловому описанию",
        "browser.fill": "заполнение поля",
        "browser.fill_by_label": "заполнение поля по подписи",
        "browser.press_key": "нажатие клавиши",
        "browser.wait": "ожидание обновления страницы",
        "browser.screenshot": "диагностический скриншот",
    }
    label = labels.get(tool_name)
    if label:
        return f"{label} ({tool_name})"
    return tool_name if _looks_russian(tool_name) else f"инструмент {tool_name}"


def _looks_russian(text: str) -> bool:
    return any("а" <= char.casefold() <= "я" or char.casefold() == "ё" for char in text)
