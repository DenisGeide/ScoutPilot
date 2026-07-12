"""CLI task sessions for single-task and interactive modes."""

from __future__ import annotations

import logging
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from scout_pilot.cli.dashboard import RuntimeDashboard
from scout_pilot.config import AppConfig
from scout_pilot.llm import (
    DeterministicBrowserMockProvider,
    DeterministicLocalDemoMockProvider,
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
from scout_pilot.runtime import (
    DEFAULT_MAX_AGENT_STEPS,
    AutonomousAgentRuntime,
    RuntimeSettings,
    TaskTerminationReason,
)
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
    max_iterations: int = DEFAULT_MAX_AGENT_STEPS
    headless: bool = False
    browser_profile_dir: Path | None = None
    slow_mo_ms: int | None = None
    mock_provider_mode: str = "default"

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("task cannot be empty")
        if self.dashboard not in {"compact", "verbose", "off"}:
            raise ValueError("dashboard must be 'compact', 'verbose' or 'off'")
        if self.provider is not None and self.provider not in {
            "openai",
            "anthropic",
            "codex",
            "mock",
        }:
            raise ValueError("provider must be 'openai', 'anthropic', 'codex' or 'mock'")
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if self.slow_mo_ms is not None and self.slow_mo_ms < 0:
            raise ValueError("slow_mo_ms cannot be negative")
        if self.mock_provider_mode not in {"default", "live_local_demo"}:
            raise ValueError("mock_provider_mode must be 'default' or 'live_local_demo'")


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
        if settings.mock_provider_mode == "default":
            provider = _create_provider(provider_name, config)
        else:
            provider = _create_provider(provider_name, config, settings.mock_provider_mode)
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
    if settings.browser_profile_dir is not None:
        browser_settings = replace(browser_settings, user_data_dir=settings.browser_profile_dir)
    if settings.slow_mo_ms is not None:
        browser_settings = replace(browser_settings, slow_mo_ms=settings.slow_mo_ms)
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

        task = UserTask(settings.task)
        while True:
            async for event in runtime.run(task):
                _record_and_render_event(settings, dashboard, recorder, event, sink)

            if not _can_resume_after_confirmation(runtime):
                break

            confirmation = runtime.pending_confirmation
            if confirmation is None:
                break

            if not _stdin_is_interactive():
                sink(
                    "Запуск не интерактивный: действие не подтверждено автоматически. "
                    "Подробности сохранены в отчете и replay."
                )
                break

            if _ask_user_confirmation(confirmation, sink):
                confirmation_id = str(confirmation.get("confirmation_id") or "")
                if runtime.confirm_pending_action(confirmation_id):
                    event = _event(
                        "confirmation_approved",
                        RuntimeStatus.RUNNING,
                        task=settings.task,
                        state="waiting_for_confirmation",
                        current_step="confirmation_approved",
                        next_action="resume_runtime",
                        success=True,
                        confirmation_id=confirmation_id,
                        message="Пользователь подтвердил одно конкретное действие.",
                        progress=_progress(0, settings.max_iterations, 0, 0),
                    )
                    _record_and_render_event(settings, dashboard, recorder, event, sink)
                    sink("Подтверждение принято. Агент выполнит только это действие один раз.")
                    continue
                sink("Не удалось применить подтверждение: действие больше не ожидает решения.")
                break

            confirmation_id = str(confirmation.get("confirmation_id") or "")
            if confirmation_id:
                runtime.reject_pending_action(confirmation_id)
            event = _event(
                "task_cancelled",
                RuntimeStatus.CANCELLED,
                task=settings.task,
                state="cancelled",
                current_step="confirmation_rejected",
                next_action="stop",
                success=False,
                confirmation_id=confirmation_id,
                message="Пользователь отменил действие, требующее подтверждения.",
                progress=_progress(0, settings.max_iterations, 0, 0),
            )
            _record_and_render_event(settings, dashboard, recorder, event, sink)
            final_message = "Действие отменено пользователем. Агент остановлен без выполнения внешнего действия."
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


def _create_provider(
    provider_name: str,
    config: AppConfig,
    mock_provider_mode: str = "default",
):
    normalized = provider_name.casefold()
    if normalized == "mock":
        if mock_provider_mode == "live_local_demo":
            return DeterministicLocalDemoMockProvider()
        return DeterministicBrowserMockProvider()
    provider = LlmProviderName(normalized)
    api_key = None
    if provider is LlmProviderName.OPENAI:
        api_key = config.provider_secrets.openai_api_key
    elif provider is LlmProviderName.ANTHROPIC:
        api_key = config.provider_secrets.anthropic_api_key
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
    if event.name == "repeated_target_blocked":
        return "Повторное открытие той же страницы пропущено. Агент выберет другой результат."
    if event.name == "repeated_target_remapped":
        return "Посещенная ссылка заменена другим непосещенным результатом того же типа."
    if event.name == "page_blocker_detected":
        return _page_blocker_message(event)
    if event.name == "modal_dismiss_started":
        return "Закрываю низкорисковое всплывающее окно."
    if event.name == "modal_dismiss_finished":
        if event.details.get("dismissed") is True:
            return "Всплывающее окно закрыто. Выполнение основной задачи продолжается."
        return "Не удалось безопасно закрыть всплывающее окно. Оно будет учтено как блокер."
    if event.name == "confirmation_required":
        return _confirmation_message(event)
    if event.name == "confirmation_approved":
        return "Подтверждение принято. Агент продолжит с одним разрешенным действием."
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
    if event.name == "repeated_target_blocked":
        target = event.details.get("target_url") or "уже посещенная страница"
        return f"Повторный переход пропущен: {target}. Следующее действие: выбрать другую ссылку."
    if event.name == "repeated_target_remapped":
        target = event.details.get("target_url") or "другой непосещенный результат"
        return f"Вместо повторного перехода выбран новый результат: {target}."
    if event.name == "page_blocker_detected":
        return _page_blocker_message(event)
    if event.name == "modal_dismiss_finished":
        if event.details.get("dismissed") is True:
            return "Низкорисковое модальное окно закрыто клавишей Escape."
        return "Модальное окно осталось открытым после безопасной попытки закрытия."
    if event.name == "confirmation_required":
        return _confirmation_message(event)
    if verbose and event.name == "reasoning_completed":
        status = event.details.get("status")
        message = event.details.get("message") or ""
        return f"Решение reasoning: {status}. {message}"
    if verbose and event.name == "context_budget_applied":
        metrics = event.details.get("metrics")
        if isinstance(metrics, dict):
            return _context_budget_message(metrics)
    if verbose and event.name in {"observation_captured", "post_action_observation_captured"}:
        title = event.details.get("title") or "без заголовка"
        url = event.details.get("url") or "URL не определен"
        return f"Наблюдение страницы: {title}; {url}"
    return ""


def _context_budget_message(metrics: Mapping[str, object]) -> str:
    before = _metric_int(metrics, "before_tokens")
    after = _metric_int(metrics, "after_tokens")
    preserved: list[str] = []
    if _metric_int(metrics, "dialogs_kept") > 0:
        preserved.append("диалоги")
    if _metric_int(metrics, "form_fields_kept") > 0:
        preserved.append("формы")
    if _metric_int(metrics, "preserved_critical_facts") > 0:
        preserved.append("важные факты")
    if not preserved and _metric_int(metrics, "observation_sections_kept") > 0:
        preserved.append("важные секции")
    if not preserved and _metric_int(metrics, "memory_summaries_kept") > 0:
        preserved.append("память задачи")
    if not preserved:
        preserved.append("безопасный минимум")
    suffix = ""
    if metrics.get("emergency_compression_applied") is True:
        suffix = " Использовано аварийное сжатие."
    return (
        f"Контекст сжат: {before} -> {after} токенов, "
        f"сохранены: {'/'.join(preserved)}.{suffix}"
    )


def _metric_int(metrics: Mapping[str, object], key: str) -> int:
    value = metrics.get(key, 0)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _tool_result_message(event: RuntimeEvent) -> str:
    tool = event.details.get("tool_name") or event.details.get("selected_tool") or "инструмент"
    success = event.details.get("success")
    if success is True:
        return f"Инструмент {tool} завершился успешно."
    message = event.details.get("message") or "подробности записаны в отчет."
    return f"Инструмент {tool} остановился: {message}"


def _page_blocker_message(event: RuntimeEvent) -> str:
    message = event.details.get("message_ru")
    if isinstance(message, str) and message.strip():
        return message
    blocker_type = event.details.get("blocker_type") or "page_blocker"
    return f"Обнаружен блокер страницы: {blocker_type}. Подробности сохранены в отчете."


def _confirmation_message(event: RuntimeEvent) -> str:
    details = event.details
    request = details.get("confirmation_request")
    if isinstance(request, dict):
        return _format_confirmation_notice(request)
    message = details.get("message_ru")
    if isinstance(message, str) and message.strip():
        return message
    return "Нужно подтверждение пользователя. Агент остановился и не продолжит автоматически."


def _safe_json(value: object) -> str:
    return json.dumps(sanitize_for_report(value), ensure_ascii=False, sort_keys=True)


def _can_resume_after_confirmation(runtime: AutonomousAgentRuntime) -> bool:
    result = runtime.last_result
    return bool(
        result is not None
        and result.termination_reason is TaskTerminationReason.WAITING_FOR_CONFIRMATION
        and runtime.pending_confirmation
    )


def _ask_user_confirmation(
    confirmation: Mapping[str, object],
    sink: ProgressSink,
) -> bool:
    sink(_format_confirmation_notice(confirmation, interactive=True))
    answer = _read_confirmation_answer("Подтвердить это действие один раз? [да/нет]: ")
    normalized = answer.strip().casefold()
    return normalized in {"y", "yes", "да", "д", "подтверждаю", "approve"}


def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


def _read_confirmation_answer(prompt: str) -> str:
    return input(prompt)


def _format_confirmation_notice(
    confirmation: Mapping[str, object],
    *,
    interactive: bool = False,
) -> str:
    confirmation_id = str(confirmation.get("confirmation_id") or "unknown")
    tool_name = str(confirmation.get("tool_name") or "unknown")
    risk = str(confirmation.get("risk") or "unknown")
    action = str(confirmation.get("action") or "выполнить действие")
    expected = str(
        confirmation.get("expected_consequence")
        or "действие может изменить состояние внешнего сервиса или пользовательские данные."
    )
    arguments = confirmation.get("redacted_arguments")
    target = _confirmation_target(arguments)
    lines = [
        "Требуется подтверждение безопасности.",
        f"ID подтверждения: {confirmation_id}",
        f"Действие: {action}",
        f"Инструмент: {tool_name}",
        f"Риск: {_risk_label_ru(risk)}",
    ]
    if target:
        lines.append(f"Цель: {target}")
    lines.extend(
        [
            f"Очищенные аргументы: {_safe_json(arguments or {})}",
            f"Почему нужна пауза: {expected}",
            (
                "Если подтвердить: агент выполнит только этот запрос инструмента один раз, "
                "после чего Security Policy снова будет проверять следующие действия."
            ),
            "Как отменить: ответьте `нет`, `n` или просто нажмите Enter; агент остановится без выполнения действия.",
        ]
    )
    if not interactive:
        lines.append("В неинтерактивном запуске действие не подтверждается автоматически.")
    return "\n".join(lines)


def _confirmation_target(arguments: object) -> str:
    if not isinstance(arguments, Mapping):
        return ""
    for key in ("target", "element_id", "url", "key"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _risk_label_ru(risk: str) -> str:
    return {
        "safe": "безопасное чтение/навигация",
        "sensitive": "может затронуть чувствительные данные",
        "destructive": "может удалить или необратимо изменить данные",
        "external_side_effect": "может отправить данные или изменить внешний сервис",
    }.get(risk, risk)


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
    if result.termination_reason is TaskTerminationReason.PARTIAL_RESULT:
        answer = result.answer or "Проверенные данные не удалось восстановить."
        return f"Защитный лимит достигнут. Показываю уже выполненную часть:\n\n{answer}"
    if result.termination_reason is TaskTerminationReason.WAITING_FOR_CONFIRMATION:
        if result.confirmation_request is not None:
            return _format_confirmation_notice(result.confirmation_request)
        return (
            "Нужно подтверждение пользователя. Агент остановился перед действием, "
            "которое может затронуть внешние системы или данные."
        )
    if result.termination_reason is TaskTerminationReason.REASONING_FAILURE:
        return (
            "Не удалось получить надежное решение от LLM-провайдера. Проверьте ключ, "
            "модель, лимиты и сеть, затем повторите задачу."
        )
    if result.termination_reason is TaskTerminationReason.PAGE_BLOCKER:
        return (
            "На странице обнаружен блокер: CAPTCHA, login wall, региональный запрос, модальное окно или похожее препятствие. "
            "Агент не обходит такие проверки, не автоматизирует логин и записывает причину в отчет."
        )
    if result.termination_reason is TaskTerminationReason.MAX_ITERATIONS_EXCEEDED:
        return (
            "Достигнут лимит автономных шагов агента. Это защитный лимит одной задачи, "
            "а не количество запущенных задач. Увеличьте --max-actions или уточните запрос."
        )
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
    if "sdk is not installed" in str(exc).casefold():
        return (
            f"Не установлен SDK для провайдера {provider_name}. Установите optional dependencies "
            '`python -m pip install -e ".[providers]"` или запустите проверку с --provider mock.'
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
