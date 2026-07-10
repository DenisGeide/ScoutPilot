"""CLI task sessions for single-task and interactive modes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from scout_pilot.cli.dashboard import RuntimeDashboard
from scout_pilot.models import RuntimeEvent, RuntimeStatus
from scout_pilot.reporting import RuntimeReportArtifacts, RuntimeReportRecorder


logger = logging.getLogger(__name__)

ProgressSink = Callable[[str], None]


@dataclass(frozen=True)
class CliTaskSettings:
    """Settings for one CLI task run."""

    task: str
    dry_run: bool
    report_path: Path
    replay_path: Path
    dashboard: str = "compact"

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("task cannot be empty")
        if self.dashboard not in {"compact", "off"}:
            raise ValueError("dashboard must be 'compact' or 'off'")


@dataclass(frozen=True)
class CliTaskRunResult:
    """User-facing result of one CLI task run."""

    success: bool
    message_ru: str
    report_path: Path
    replay_path: Path
    dry_run: bool


async def run_cli_task(
    settings: CliTaskSettings,
    *,
    progress: ProgressSink | None = None,
) -> CliTaskRunResult:
    """Run one CLI task through a deterministic dry-run session."""

    sink = progress or (lambda _message: None)
    recorder = RuntimeReportRecorder(
        task=settings.task,
        mode="cli_single_task",
        dry_run=settings.dry_run,
    )
    dashboard = RuntimeDashboard(task=settings.task)

    logger.info(
        "cli_task_started",
        extra={"event": "cli_task_started", "dry_run": settings.dry_run},
    )

    if not settings.dry_run:
        message_ru = (
            "Режим с реальными браузерными действиями из команды run пока не подключен. "
            "Для безопасной проверки повторите команду с --dry-run. Для браузерной "
            "демонстрации используйте interview-demo или demo-vacancy-search."
        )
        event = _event(
            "task_failed",
            RuntimeStatus.FAILED,
            task=settings.task,
            state="failed",
            current_step="live mode unavailable",
            next_action="rerun with --dry-run",
            success=False,
            message=message_ru,
            progress=_progress(0, 1, 0, 1),
        )
        recorder.record_event(event)
        recorder.finalize(success=False, summary_ru=message_ru, failure_ru=message_ru)
        artifacts = recorder.write(
            report_path=settings.report_path,
            replay_path=settings.replay_path,
        )
        _render_event(settings, dashboard, event, sink)
        sink(message_ru)
        return CliTaskRunResult(
            success=False,
            message_ru=message_ru,
            report_path=artifacts.report_path,
            replay_path=artifacts.replay_path,
            dry_run=False,
        )

    events = _dry_run_events(settings.task)
    for event in events:
        recorder.record_event(event)
        _render_event(settings, dashboard, event, sink)

    summary_ru = (
        "Сухой запуск завершен: задача принята, план действий показан, "
        "браузерные действия, вызовы LLM и отправка данных не выполнялись."
    )
    recorder.finalize(success=True, summary_ru=summary_ru)
    artifacts = recorder.write(
        report_path=settings.report_path,
        replay_path=settings.replay_path,
    )
    sink(summary_ru)
    sink(f"Отчет: {artifacts.report_path}")
    sink(f"Replay-файл: {artifacts.replay_path}")

    logger.info(
        "cli_task_completed",
        extra={"event": "cli_task_completed", "dry_run": settings.dry_run},
    )
    return CliTaskRunResult(
        success=True,
        message_ru=summary_ru,
        report_path=artifacts.report_path,
        replay_path=artifacts.replay_path,
        dry_run=True,
    )


def default_artifact_paths(report_dir: Path, *, prefix: str = "task") -> RuntimeReportArtifacts:
    """Return timestamped report and replay paths under a private report directory."""

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = uuid4().hex[:8]
    return RuntimeReportArtifacts(
        report_path=report_dir / f"{prefix}-{stamp}-{run_id}-report.json",
        replay_path=report_dir / f"{prefix}-{stamp}-{run_id}-replay.json",
    )


def _render_event(
    settings: CliTaskSettings,
    dashboard: RuntimeDashboard,
    event: RuntimeEvent,
    sink: ProgressSink,
) -> None:
    if settings.dashboard == "off":
        message = _compact_progress_message(event)
        if message:
            sink(message)
        return
    sink(dashboard.render_event(event))


def _compact_progress_message(event: RuntimeEvent) -> str:
    if event.name == "task_started":
        return "Задача принята."
    if event.name == "plan_created":
        return "План сухого запуска подготовлен."
    if event.name == "tool_selected":
        return "Инструмент выбран только для показа; выполнение пропущено."
    if event.name == "task_completed":
        return "Задача завершена в режиме сухого запуска."
    if event.name == "task_failed":
        return "Задача остановлена."
    return ""


def _dry_run_events(task: str) -> tuple[RuntimeEvent, ...]:
    task_id = uuid4().hex
    return (
        _event(
            "task_started",
            RuntimeStatus.RUNNING,
            task_id=task_id,
            task=task,
            state="idle",
            current_step="accept_task",
            next_action="prepare_plan",
            progress=_progress(0, 4, 0, 4),
        ),
        _event(
            "state_transition",
            RuntimeStatus.RUNNING,
            task_id=task_id,
            task=task,
            state="planning",
            from_state="idle",
            to_state="planning",
            reason="Prepare a safe high-level plan for the user task.",
            current_step="prepare_plan",
            next_action="summarize_constraints",
            progress=_progress(1, 4, 1, 4),
        ),
        _event(
            "plan_created",
            RuntimeStatus.RUNNING,
            task_id=task_id,
            task=task,
            state="planning",
            current_step="plan_created",
            next_action="prepare_observation",
            progress=_progress(1, 4, 1, 4),
            plan_summary="План сухого запуска подготовлен без браузера и LLM-вызовов.",
            steps=[
                "Понять задачу и ограничения безопасности.",
                "Читать страницу только в режиме с реальным браузером.",
                "Выбирать действия через Tool Runtime.",
                "Останавливаться перед действиями, требующими подтверждения.",
            ],
        ),
        _event(
            "state_transition",
            RuntimeStatus.RUNNING,
            task_id=task_id,
            task=task,
            state="observing",
            from_state="planning",
            to_state="observing",
            reason="Dry run does not open the browser; observation is represented as a placeholder.",
            current_step="prepare_placeholder_observation",
            next_action="evaluate_next_action",
            progress=_progress(2, 4, 2, 4),
        ),
        _event(
            "observation_captured",
            RuntimeStatus.RUNNING,
            task_id=task_id,
            task=task,
            state="observing",
            current_step="placeholder_observation",
            next_action="select_tool",
            progress=_progress(2, 4, 2, 4),
            observation_summary=(
                "Сухой запуск: браузер не открывался, raw HTML, скриншоты и локальные "
                "данные браузера не читались."
            ),
        ),
        _event(
            "tool_selected",
            RuntimeStatus.RUNNING,
            task_id=task_id,
            task=task,
            state="executing",
            current_step="select_tool",
            selected_tool="browser.observe",
            next_action="skip_execution",
            progress=_progress(3, 4, 3, 4),
            dry_run_skipped=True,
            reason="Dry run shows intended tool boundary without executing browser actions.",
        ),
        _event(
            "reasoning_completed",
            RuntimeStatus.RUNNING,
            task_id=task_id,
            task=task,
            state="reasoning",
            current_step="prepare_summary",
            next_action="write_report",
            progress=_progress(3, 4, 3, 4),
            message="Сухой запуск завершен без вызовов LLM-провайдера.",
        ),
        _event(
            "task_completed",
            RuntimeStatus.COMPLETED,
            task_id=task_id,
            task=task,
            state="completed",
            current_step="final_summary",
            selected_tool="browser.observe",
            next_action="review_report",
            progress=_progress(4, 4, 4, 4),
            success=True,
            answer=(
                "Это был только сухой запуск: браузерные действия, вызовы LLM, отправка "
                "форм и внешние эффекты не выполнялись."
            ),
        ),
    )


def _event(name: str, status: RuntimeStatus, **details: object) -> RuntimeEvent:
    return RuntimeEvent(name=name, status=status, details=details)


def _progress(
    iteration: int,
    max_iterations: int,
    completed_steps: int,
    total_steps: int,
) -> dict[str, int]:
    return {
        "iteration": iteration,
        "max_iterations": max_iterations,
        "failure_count": 0,
        "max_failures": 0,
        "completed_steps": completed_steps,
        "total_steps": total_steps,
    }
