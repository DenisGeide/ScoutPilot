"""Console status rendering for CLI runtime sessions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from scout_pilot.models import RuntimeEvent
from scout_pilot.reporting import sanitize_for_report


@dataclass
class RuntimeDashboardState:
    """Current CLI-facing task status."""

    task: str = ""
    state: str = "ожидание"
    current_step: str = "ожидание задачи"
    iteration: str = "0/0"
    plan_step: str = "-"
    observation_summary: str = "-"
    selected_tool: str = "-"
    tool_arguments: str = "-"
    security_decision: str = "-"
    result_status: str = "-"
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
        plan_step = _plan_step_from_event(event, details)
        if plan_step:
            self.state.plan_step = _short_text(plan_step, 180)
        observation_summary = _observation_summary_from_event(event, details)
        if observation_summary:
            self.state.observation_summary = _short_text(observation_summary, 220)
        self.state.next_action = _action_label(
            str(details.get("next_action") or "")
        ) or _next_action(event)
        selected_tool = details.get("selected_tool") or details.get("tool_name")
        if selected_tool:
            self.state.selected_tool = _tool_label(str(selected_tool))
        if "selected_tool_arguments" in details:
            tool_arguments = details.get("selected_tool_arguments")
        else:
            tool_arguments = details.get("tool_arguments")
        if tool_arguments is None and event.name == "tool_selected":
            tool_arguments = {}
        if tool_arguments is not None:
            self.state.tool_arguments = _safe_json(tool_arguments)
        security_decision = _security_decision_from_event(event, details)
        if security_decision:
            self.state.security_decision = security_decision
        result_status = _result_status_from_event(event, details)
        if result_status:
            self.state.result_status = result_status
        progress = details.get("progress")
        if isinstance(progress, Mapping):
            self.state.progress = _progress_text(progress)
            self.state.iteration = _iteration_text(progress)
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
                f"Итерация: {self.state.iteration}",
                f"Текущий шаг: {self.state.current_step}",
                f"Шаг плана: {self.state.plan_step}",
                f"Краткое наблюдение: {self.state.observation_summary}",
                f"Выбранный инструмент: {self.state.selected_tool}",
                f"Аргументы инструмента: {self.state.tool_arguments}",
                f"Решение безопасности: {self.state.security_decision}",
                f"Статус результата: {self.state.result_status}",
                f"Прогресс: {self.state.progress}",
                f"Прошло: {self.state.elapsed_seconds:.1f} с",
                f"Следующее действие: {self.state.next_action}",
            )
        )

    def render_event(self, event: RuntimeEvent) -> str:
        self.update(event)
        return self.render()

    def trace(self) -> Mapping[str, object]:
        """Return the safe user-facing dashboard state for reports and replay."""

        return sanitize_for_report(
            {
                "task": self.state.task,
                "state": self.state.state,
                "iteration": self.state.iteration,
                "current_step": self.state.current_step,
                "plan_step": self.state.plan_step,
                "observation_summary": self.state.observation_summary,
                "selected_tool": self.state.selected_tool,
                "tool_arguments": self.state.tool_arguments,
                "security_decision": self.state.security_decision,
                "result_status": self.state.result_status,
                "progress": self.state.progress,
                "elapsed_seconds": round(self.state.elapsed_seconds, 1),
                "next_action": self.state.next_action,
            }
        )


def _progress_text(progress: Mapping[object, object]) -> str:
    completed = progress.get("completed_steps", 0)
    total = progress.get("total_steps", 0)
    iteration = progress.get("iteration", 0)
    max_iterations = progress.get("max_iterations", 0)
    if total:
        return f"{completed}/{total} шагов, итерация {iteration}/{max_iterations}"
    return f"итерация {iteration}/{max_iterations}"


def _iteration_text(progress: Mapping[object, object]) -> str:
    iteration = progress.get("iteration", 0)
    max_iterations = progress.get("max_iterations", 0)
    return f"{iteration}/{max_iterations}"


def _plan_step_from_event(
    event: RuntimeEvent,
    details: Mapping[str, object],
) -> str:
    explicit = details.get("current_plan_step") or details.get("plan_step")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    if event.name not in {"plan_created", "plan_revised"}:
        return ""
    steps = details.get("steps")
    if isinstance(steps, Sequence) and not isinstance(steps, str | bytes | bytearray):
        first_step = next(iter(steps), None)
        if isinstance(first_step, Mapping):
            goal = first_step.get("goal")
            if isinstance(goal, str) and goal.strip():
                return goal
        if first_step is not None:
            return str(first_step)
    summary = details.get("summary") or details.get("plan_summary")
    return str(summary) if summary else ""


def _observation_summary_from_event(
    event: RuntimeEvent,
    details: Mapping[str, object],
) -> str:
    if event.name not in {"observation_captured", "post_action_observation_captured"}:
        return ""
    summary = details.get("observation_summary") or details.get("summary")
    return str(summary) if summary else ""


def _security_decision_from_event(
    event: RuntimeEvent,
    details: Mapping[str, object],
) -> str:
    decision = details.get("security_decision")
    if isinstance(decision, Mapping):
        status = str(decision.get("status") or "")
        reason = decision.get("reason")
        base = _security_status_label(status)
        if isinstance(reason, str) and reason.strip():
            return f"{base}: {_short_text(reason, 140)}"
        return base
    if event.name == "tool_selected":
        return "ожидает проверки перед выполнением"
    if event.name == "confirmation_required":
        return "требуется подтверждение пользователя"
    if event.name == "tool_execution_finished":
        status = str(details.get("tool_status") or "")
        if status == "paused":
            return "требуется подтверждение пользователя"
        if status == "blocked":
            return "действие заблокировано политикой безопасности"
        if details.get("success") is True:
            return "разрешено политикой безопасности"
    return ""


def _security_status_label(status: str) -> str:
    return {
        "allow": "разрешено политикой безопасности",
        "pause": "требуется подтверждение пользователя",
        "block": "действие заблокировано политикой безопасности",
        "not_run_validation": "не выполнялось: ошибка валидации до действия",
    }.get(status, status or "решение безопасности получено")


def _result_status_from_event(
    event: RuntimeEvent,
    details: Mapping[str, object],
) -> str:
    if event.name == "tool_selected":
        return "инструмент выбран, выполнение еще не началось"
    if event.name == "confirmation_required":
        return "выполнение приостановлено до решения пользователя"
    if event.name == "reflection_completed":
        outcome = details.get("outcome")
        recommended_action = details.get("recommended_action")
        if outcome or recommended_action:
            return f"оценка: {outcome}; дальше: {recommended_action}"
    if event.name != "tool_execution_finished":
        return ""
    status = str(details.get("tool_status") or "")
    success = details.get("success")
    message = details.get("message")
    if success is True:
        base = "успешно"
    elif status == "paused":
        base = "пауза перед внешним эффектом"
    elif status == "blocked":
        base = "заблокировано"
    else:
        base = status or "ошибка"
    if isinstance(message, str) and message.strip():
        return f"{base}: {_short_text(message, 140)}"
    return base


def _safe_json(value: object) -> str:
    return json.dumps(sanitize_for_report(value), ensure_ascii=False, sort_keys=True)


def _short_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(limit - 1, 0)]}..."


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
