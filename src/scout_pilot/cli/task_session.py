"""CLI task sessions for single-task and interactive modes."""

from __future__ import annotations

import logging
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from scout_pilot.cli.dashboard import RuntimeDashboard
from scout_pilot.config import AppConfig
from scout_pilot.llm import (
    DeterministicBrowserMockProvider,
    LlmProviderConfig,
    LlmProviderName,
    ReasoningEngine,
    ReasoningSettings,
    create_llm_provider,
)
from scout_pilot.models import RuntimeEvent, RuntimeStatus, ToolRequest, UserTask
from scout_pilot.planning import ProviderPlanningEngine
from scout_pilot.planning.types import PlanningSettings
from scout_pilot.reporting import (
    RuntimeReportArtifacts,
    RuntimeReportRecorder,
    sanitize_for_report,
)
from scout_pilot.runtime import AutonomousAgentRuntime, RuntimeSettings, TaskTerminationReason
from scout_pilot.tools.types import ToolExecutionResult, ToolExecutionStatus, ToolSchema


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
    start_url: str | None = None
    provider: str | None = None
    max_iterations: int = 8
    headless: bool = False

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("task cannot be empty")
        if self.dashboard not in {"compact", "verbose", "off"}:
            raise ValueError("dashboard must be 'compact', 'verbose' or 'off'")
        if self.provider is not None and self.provider not in {"openai", "anthropic", "mock"}:
            raise ValueError("provider must be 'openai', 'anthropic' or 'mock'")
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")


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
    """Run one CLI task through dry-run or live autonomous runtime."""

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
        return await _run_live_task(settings, recorder, dashboard, sink)

    events = _dry_run_events(settings.task)
    for event in events:
        _record_and_render_event(settings, dashboard, recorder, event, sink)

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


async def _run_live_task(
    settings: CliTaskSettings,
    recorder: RuntimeReportRecorder,
    dashboard: RuntimeDashboard,
    sink: ProgressSink,
) -> CliTaskRunResult:
    from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
    from scout_pilot.memory import HierarchicalMemory
    from scout_pilot.observation import ObservationSettings, SemanticObservationEngine
    from scout_pilot.tools import DefaultToolRuntime, ToolContext, create_browser_tool_registry

    config = AppConfig.load()
    provider_name = settings.provider or config.llm_provider.casefold()
    try:
        provider = _create_provider(provider_name, config)
    except Exception as exc:
        message_ru = _provider_start_error_ru(provider_name, exc)
        event = _event(
            "task_failed",
            RuntimeStatus.FAILED,
            task=settings.task,
            state="failed",
            current_step="provider_start_failed",
            next_action="check_provider_configuration",
            success=False,
            message=message_ru,
            error_type=type(exc).__name__,
            progress=_progress(0, settings.max_iterations, 0, 0),
        )
        _record_and_render_event(settings, dashboard, recorder, event, sink)
        recorder.finalize(success=False, summary_ru=message_ru, failure_ru=message_ru)
        artifacts = recorder.write(
            report_path=settings.report_path,
            replay_path=settings.replay_path,
        )
        sink(message_ru)
        sink(f"Отчет: {artifacts.report_path}")
        sink(f"Replay-файл: {artifacts.replay_path}")
        return CliTaskRunResult(
            success=False,
            message_ru=message_ru,
            report_path=artifacts.report_path,
            replay_path=artifacts.replay_path,
            dry_run=False,
        )

    browser_settings = replace(
        BrowserEngineConfig.from_app_config(config),
        headless=settings.headless,
    )
    browser = PlaywrightBrowserEngine(browser_settings)
    observation_engine = SemanticObservationEngine(
        browser,
        ObservationSettings.from_app_config(config),
    )
    registry = create_browser_tool_registry()
    tool_runtime = DefaultToolRuntime(
        registry,
        ToolContext(browser=browser, observation_engine=observation_engine),
    )
    tool_schemas = registry.schemas()
    runtime = AutonomousAgentRuntime(
        observation_engine=observation_engine,
        reasoning_engine=ReasoningEngine(
            provider,
            ReasoningSettings(
                model=config.llm_model,
                max_output_tokens=config.llm_max_output_tokens,
                timeout_seconds=config.llm_timeout_seconds,
                max_input_tokens=config.max_context_tokens,
            ),
        ),
        planning_engine=ProviderPlanningEngine(
            provider,
            PlanningSettings(
                max_input_tokens=config.max_context_tokens,
                max_output_tokens=config.llm_max_output_tokens,
                timeout_seconds=config.llm_timeout_seconds,
            ),
        ),
        tool_runtime=tool_runtime,
        memory=HierarchicalMemory(),
        tool_schemas=tool_schemas,
        settings=RuntimeSettings(max_iterations=settings.max_iterations),
        security_constraints=(
            "Перед отправкой форм, откликами, сообщениями, покупками, загрузкой файлов "
            "или удалением данных нужно явное подтверждение пользователя."
        ),
        confirmation_constraints=(
            "Никогда не продолжай автоматически после confirmation_required.",
        ),
        budget={"remaining_tokens": config.max_context_tokens},
    )

    sink("Запускаю live-режим: браузер будет открыт, действия проходят через Tool Runtime.")
    if provider_name == "mock":
        sink("LLM-провайдер: mock. Внешние API-вызовы не выполняются.")
    else:
        sink(f"LLM-провайдер: {provider_name}. Автоматические тесты этот режим не используют.")
    if settings.start_url:
        sink(f"Стартовая страница: {settings.start_url}")

    final_message = "Задача остановлена до формирования результата."
    success = False
    try:
        await browser.start()
        if settings.start_url:
            navigation = await _execute_initial_navigation(
                settings,
                dashboard,
                recorder,
                sink,
                tool_runtime,
                tool_schemas,
            )
            if not navigation.success:
                final_message = (
                    "Не удалось открыть стартовую страницу. Проверьте URL, сеть и доступность сайта."
                )
                recorder.finalize(
                    success=False,
                    summary_ru=final_message,
                    failure_ru=final_message,
                )
                artifacts = recorder.write(
                    report_path=settings.report_path,
                    replay_path=settings.replay_path,
                )
                sink(final_message)
                sink(f"Отчет: {artifacts.report_path}")
                sink(f"Replay-файл: {artifacts.replay_path}")
                return CliTaskRunResult(
                    success=False,
                    message_ru=final_message,
                    report_path=artifacts.report_path,
                    replay_path=artifacts.replay_path,
                    dry_run=False,
                )

        async for event in runtime.run(UserTask(settings.task)):
            _record_and_render_event(settings, dashboard, recorder, event, sink)

        if runtime.last_result is None:
            final_message = "Runtime завершился без итогового результата."
            success = False
        else:
            success = runtime.last_result.success
            final_message = _result_message_ru(runtime.last_result)
        recorder.finalize(
            success=success,
            summary_ru=final_message,
            failure_ru=None if success else final_message,
        )
        artifacts = recorder.write(
            report_path=settings.report_path,
            replay_path=settings.replay_path,
        )
        sink(final_message)
        sink(f"Отчет: {artifacts.report_path}")
        sink(f"Replay-файл: {artifacts.replay_path}")
        return CliTaskRunResult(
            success=success,
            message_ru=final_message,
            report_path=artifacts.report_path,
            replay_path=artifacts.replay_path,
            dry_run=False,
        )
    except KeyboardInterrupt:
        runtime.cancel("User cancelled from CLI.")
        final_message = "Задача отменена пользователем. Браузер будет закрыт."
        event = _event(
            "task_cancelled",
            RuntimeStatus.CANCELLED,
            task=settings.task,
            state="cancelled",
            current_step="user_cancelled",
            success=False,
            message=final_message,
            progress=_progress(0, settings.max_iterations, 0, 0),
        )
        _record_and_render_event(settings, dashboard, recorder, event, sink)
        recorder.finalize(success=False, summary_ru=final_message, failure_ru=final_message)
        artifacts = recorder.write(
            report_path=settings.report_path,
            replay_path=settings.replay_path,
        )
        sink(final_message)
        return CliTaskRunResult(
            success=False,
            message_ru=final_message,
            report_path=artifacts.report_path,
            replay_path=artifacts.replay_path,
            dry_run=False,
        )
    except Exception as exc:
        final_message = _unexpected_live_error_ru(exc)
        event = _event(
            "task_failed",
            RuntimeStatus.FAILED,
            task=settings.task,
            state="failed",
            current_step="live_runtime_error",
            next_action="check_report_and_debug_logs",
            success=False,
            message=final_message,
            error_type=type(exc).__name__,
            progress=_progress(0, settings.max_iterations, 0, 0),
        )
        _record_and_render_event(settings, dashboard, recorder, event, sink)
        recorder.finalize(success=False, summary_ru=final_message, failure_ru=final_message)
        artifacts = recorder.write(
            report_path=settings.report_path,
            replay_path=settings.replay_path,
        )
        sink(final_message)
        sink(f"Отчет: {artifacts.report_path}")
        sink(f"Replay-файл: {artifacts.replay_path}")
        return CliTaskRunResult(
            success=False,
            message_ru=final_message,
            report_path=artifacts.report_path,
            replay_path=artifacts.replay_path,
            dry_run=False,
        )
    finally:
        await browser.stop()
        sink("Браузер закрыт.")


async def _execute_initial_navigation(
    settings: CliTaskSettings,
    dashboard: RuntimeDashboard,
    recorder: RuntimeReportRecorder,
    sink: ProgressSink,
    tool_runtime,
    schemas: tuple[ToolSchema, ...],
) -> ToolExecutionResult:
    request = ToolRequest("browser.navigate", {"url": settings.start_url})
    selected = _tool_selected_event(
        settings,
        request,
        schemas,
        next_action="execute_tool",
    )
    _record_and_render_event(settings, dashboard, recorder, selected, sink)
    result = await tool_runtime.execute(request)
    event = _event(
        "tool_execution_finished",
        RuntimeStatus.RUNNING if result.success else RuntimeStatus.FAILED,
        task=settings.task,
        state="executing",
        tool_name=result.tool_name,
        tool_status=result.status.value,
        success=result.success,
        message=result.message,
        retryable=result.retryable,
        error_code=result.error_code,
        progress=_progress(0, settings.max_iterations, 0, 0),
    )
    _record_and_render_event(settings, dashboard, recorder, event, sink)
    return result


def _create_provider(provider_name: str, config: AppConfig):
    normalized = provider_name.casefold()
    if normalized == "mock":
        return DeterministicBrowserMockProvider()
    provider = LlmProviderName(normalized)
    api_key = (
        config.provider_secrets.openai_api_key
        if provider is LlmProviderName.OPENAI
        else config.provider_secrets.anthropic_api_key
    )
    return create_llm_provider(
        LlmProviderConfig(
            provider=provider,
            model=config.llm_model,
            timeout_seconds=config.llm_timeout_seconds,
            max_output_tokens=config.llm_max_output_tokens,
            api_key=api_key,
        )
    )


def default_artifact_paths(report_dir: Path, *, prefix: str = "task") -> RuntimeReportArtifacts:
    """Return timestamped report and replay paths under a private report directory."""

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = uuid4().hex[:8]
    return RuntimeReportArtifacts(
        report_path=report_dir / f"{prefix}-{stamp}-{run_id}-report.json",
        replay_path=report_dir / f"{prefix}-{stamp}-{run_id}-replay.json",
    )


def _record_and_render_event(
    settings: CliTaskSettings,
    dashboard: RuntimeDashboard,
    recorder: RuntimeReportRecorder,
    event: RuntimeEvent,
    sink: ProgressSink,
) -> None:
    dashboard.update(event)
    recorder.record_event(_event_with_trace(event, dashboard.trace()))
    _render_event(settings, dashboard, event, sink, already_updated=True)


def _event_with_trace(
    event: RuntimeEvent,
    trace: object,
) -> RuntimeEvent:
    return RuntimeEvent(
        name=event.name,
        status=event.status,
        details={**dict(event.details), "trace": trace},
    )


def _render_event(
    settings: CliTaskSettings,
    dashboard: RuntimeDashboard,
    event: RuntimeEvent,
    sink: ProgressSink,
    *,
    already_updated: bool = False,
) -> None:
    if settings.dashboard == "off":
        message = _compact_progress_message(event)
        if message:
            sink(message)
        return
    if not already_updated:
        dashboard.update(event)
    sink(dashboard.render())
    detail = _event_detail_message(event, verbose=settings.dashboard == "verbose")
    if detail:
        sink(detail)


def _compact_progress_message(event: RuntimeEvent) -> str:
    if event.name == "task_started":
        return "Задача принята."
    if event.name == "plan_created":
        return "План выполнения подготовлен."
    if event.name == "tool_selected":
        tool = event.details.get("selected_tool") or event.details.get("tool_name")
        arguments = event.details.get("selected_tool_arguments")
        if arguments:
            return f"Выбран инструмент {tool}: {_safe_json(arguments)}"
        return f"Выбран инструмент {tool}."
    if event.name == "tool_execution_finished":
        return _tool_result_message(event)
    if event.name == "confirmation_required":
        return _confirmation_message(event)
    if event.name == "task_completed":
        return "Задача завершена."
    if event.name == "task_failed":
        return "Задача остановлена."
    return ""


def _event_detail_message(event: RuntimeEvent, *, verbose: bool) -> str:
    if event.name == "tool_selected":
        tool = event.details.get("selected_tool") or event.details.get("tool_name")
        arguments = event.details.get("selected_tool_arguments")
        return f"Инструмент перед выполнением: {tool}; аргументы: {_safe_json(arguments or {})}"
    if event.name == "tool_execution_finished":
        return _tool_result_message(event)
    if event.name == "confirmation_required":
        return _confirmation_message(event)
    if verbose and event.name == "reasoning_completed":
        status = event.details.get("status")
        message = event.details.get("message") or ""
        return f"Решение reasoning: {status}. {message}"
    if verbose and event.name == "context_budget_applied":
        metrics = event.details.get("metrics")
        if isinstance(metrics, dict):
            before = metrics.get("before_tokens")
            after = metrics.get("after_tokens")
            return f"Контекст проверен и сжат при необходимости: {before} -> {after} токенов."
    if verbose and event.name in {"observation_captured", "post_action_observation_captured"}:
        title = event.details.get("title") or "без заголовка"
        url = event.details.get("url") or "URL не определен"
        return f"Наблюдение страницы: {title}; {url}"
    return ""


def _tool_result_message(event: RuntimeEvent) -> str:
    tool = event.details.get("tool_name") or event.details.get("selected_tool") or "инструмент"
    success = event.details.get("success")
    if success is True:
        return f"Инструмент {tool} завершился успешно."
    message = event.details.get("message") or "подробности записаны в отчет."
    return f"Инструмент {tool} остановился: {message}"


def _confirmation_message(event: RuntimeEvent) -> str:
    details = event.details
    message = details.get("message_ru")
    if isinstance(message, str) and message.strip():
        return message
    request = details.get("confirmation_request")
    if isinstance(request, dict):
        request_message = request.get("message_ru")
        if isinstance(request_message, str) and request_message.strip():
            return request_message
    return "Нужно подтверждение пользователя. Агент остановился и не продолжит автоматически."


def _safe_json(value: object) -> str:
    return json.dumps(sanitize_for_report(value), ensure_ascii=False, sort_keys=True)


def _tool_selected_event(
    settings: CliTaskSettings,
    request: ToolRequest,
    schemas: tuple[ToolSchema, ...],
    *,
    next_action: str,
) -> RuntimeEvent:
    return _event(
        "tool_selected",
        RuntimeStatus.RUNNING,
        task=settings.task,
        state="executing",
        selected_tool=request.name,
        selected_tool_arguments=_redact_tool_arguments(request, schemas),
        next_action=next_action,
        progress=_progress(0, settings.max_iterations, 0, 0),
    )


def _redact_tool_arguments(
    request: ToolRequest,
    schemas: tuple[ToolSchema, ...],
) -> dict[str, object]:
    schema = next((schema for schema in schemas if schema.name == request.name), None)
    sensitive_fields = (
        schema.input_schema.sensitive_field_names() if schema is not None else set()
    )
    redacted: dict[str, object] = {}
    for key, value in request.arguments.items():
        if key in sensitive_fields or key.casefold() in {
            "value",
            "password",
            "token",
            "secret",
            "api_key",
            "cookie",
        }:
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted


def _result_message_ru(result) -> str:
    if result.termination_reason is TaskTerminationReason.ANSWERED:
        return result.answer or "Задача завершена."
    if result.termination_reason is TaskTerminationReason.WAITING_FOR_CONFIRMATION:
        if result.confirmation_request is not None:
            message = result.confirmation_request.get("message_ru")
            if isinstance(message, str) and message.strip():
                return message
        return (
            "Нужно подтверждение пользователя. Агент остановился перед действием, "
            "которое может затронуть внешние системы или данные."
        )
    if result.termination_reason is TaskTerminationReason.REASONING_FAILURE:
        return (
            "Не удалось получить надежное решение от LLM-провайдера. Проверьте ключ, "
            "модель, лимиты и сеть, затем повторите задачу."
        )
    if result.termination_reason is TaskTerminationReason.MAX_ITERATIONS_EXCEEDED:
        return "Достигнут лимит итераций. Попробуйте сузить задачу или задать стартовый URL."
    if result.termination_reason is TaskTerminationReason.MAX_FAILURES_EXCEEDED:
        return "Достигнут лимит повторных ошибок. Агент остановился, чтобы не зациклиться."
    if result.termination_reason is TaskTerminationReason.CANCELLED:
        return "Задача отменена пользователем."
    return result.message or "Задача остановлена. Подробности сохранены в отчете."


def _provider_start_error_ru(provider_name: str, exc: Exception) -> str:
    if isinstance(exc, ValueError) and "API key" in str(exc):
        return (
            f"Не настроен API-ключ для провайдера {provider_name}. Добавьте ключ в локальный "
            ".env или запустите проверку с --provider mock."
        )
    return (
        f"Не удалось подготовить LLM-провайдера {provider_name}: {type(exc).__name__}. "
        "Проверьте зависимости, настройки .env и выбранную модель."
    )


def _unexpected_live_error_ru(exc: Exception) -> str:
    return (
        f"Live-режим остановился из-за ошибки {type(exc).__name__}. "
        "Браузер будет закрыт, безопасный отчет и replay сохранены."
    )


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
