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
    state: str = "idle"
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
        self.state.state = str(details.get("state") or self.state.state)
        self.state.current_step = _step_text(event)
        self.state.next_action = str(details.get("next_action") or _next_action(event))
        selected_tool = details.get("selected_tool") or details.get("tool_name")
        if selected_tool:
            self.state.selected_tool = str(selected_tool)
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
    return mapping.get(event.name, event.name.replace("_", " "))


def _next_action(event: RuntimeEvent) -> str:
    mapping = {
        "task_started": "подготовить план выполнения",
        "plan_created": "проверить, какие данные нужны перед действиями",
        "observation_captured": "оценить доступный безопасный контекст",
        "tool_selected": "показать выбранное действие без выполнения",
        "reasoning_completed": "сформировать итог сухого запуска",
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
        "completed": "завершение",
        "failed": "остановка",
    }.get(state, state or "обновление состояния")


def _state_next_action(state: str) -> str:
    return {
        "planning": "собрать короткий безопасный план",
        "observing": "не открывать браузер в dry-run режиме",
        "reasoning": "проверить ограничения безопасности",
        "executing": "не выполнять выбранный инструмент в dry-run режиме",
        "completed": "сохранить report и replay",
        "failed": "показать понятную причину",
    }.get(state, "продолжить")
