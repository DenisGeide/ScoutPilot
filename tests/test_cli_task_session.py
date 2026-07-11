import asyncio
import json
from pathlib import Path

from scout_pilot.cli.dashboard import RuntimeDashboard
from scout_pilot.cli import task_session
from scout_pilot.cli.task_session import CliTaskSettings, run_cli_task
from scout_pilot.demo.interview import LocalDemoServer, prepare_local_interview_site
from scout_pilot.llm.types import (
    LlmFinishReason,
    LlmProviderRequest,
    LlmProviderResponse,
    LlmProviderResult,
    LlmToolCall,
)
from scout_pilot.models import RuntimeEvent, RuntimeStatus


def test_dry_run_task_generates_safe_report_and_replay(tmp_path):
    report_path = tmp_path / "report.json"
    replay_path = tmp_path / "replay.json"
    messages: list[str] = []

    result = asyncio.run(
        run_cli_task(
            CliTaskSettings(
                task=r"Найди вакансии token=private-value C:\Users\Unknown\Desktop\secret.txt",
                dry_run=True,
                report_path=report_path,
                replay_path=replay_path,
                dashboard="off",
            ),
            progress=messages.append,
        )
    )

    assert result.success is True
    assert report_path.exists()
    assert replay_path.exists()
    assert any("Сухой запуск завершен" in message for message in messages)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    serialized = json.dumps(report, ensure_ascii=False).casefold()

    assert report["artifact_kind"] == "runtime_report"
    assert replay["artifact_kind"] == "runtime_replay"
    assert report["dry_run"] is True
    assert report["final"]["success"] is True
    assert all("trace" in event["details"] for event in report["events"])
    assert all("trace" in event["details"] for event in replay["events"])
    assert "браузерные действия" in serialized
    assert "private-value" not in serialized
    assert "secret.txt" not in serialized
    assert "<html" not in serialized
    assert "browser profile" not in serialized


def test_live_cli_mode_reports_missing_provider_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")

    result = asyncio.run(
        run_cli_task(
            CliTaskSettings(
                task="Открой сайт и найди информацию",
                dry_run=False,
                report_path=tmp_path / "report.json",
                replay_path=tmp_path / "replay.json",
                dashboard="off",
                provider="openai",
            )
        )
    )

    assert result.success is False
    assert "API-ключ" in result.message_ru
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["final"]["success"] is False
    assert "provider mock" in report["final"]["failure_ru"]


def test_live_cli_provider_start_error_mentions_provider_extra():
    message = task_session._provider_start_error_ru(
        "openai",
        RuntimeError("OpenAI SDK is not installed."),
    )

    assert "providers" in message
    assert "--provider mock" in message


def test_live_cli_mock_provider_runs_runtime_on_local_page(tmp_path):
    site = prepare_local_interview_site(tmp_path / "site")
    report_path = tmp_path / "live-report.json"
    replay_path = tmp_path / "live-replay.json"
    messages: list[str] = []

    with LocalDemoServer(site.root) as server:
        result = asyncio.run(
            run_cli_task(
                CliTaskSettings(
                    task="Найди 3 вакансии на локальном тестовом сайте и подготовь заметки",
                    dry_run=False,
                    report_path=report_path,
                    replay_path=replay_path,
                    dashboard="off",
                    provider="mock",
                    start_url=server.url_for(site.start_page_name),
                    max_iterations=3,
                    headless=True,
                ),
                progress=messages.append,
            )
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    serialized = json.dumps(report, ensure_ascii=False).casefold()
    event_names = [event["name"] for event in report["events"]]

    assert result.success is True
    assert report["dry_run"] is False
    assert report["final"]["success"] is True
    assert "Проверочный live-запуск завершен" in result.message_ru
    assert "tool_selected" in event_names
    assert "observation_captured" in event_names
    assert "tool_execution_finished" in event_names
    assert replay["artifact_kind"] == "runtime_replay"
    assert any("Выбран инструмент browser.observe" in message for message in messages)
    assert "<html" not in serialized
    assert "<button" not in serialized
    assert "private-value" not in serialized
    assert "browser profile" not in serialized


def test_live_cli_surfaces_security_pause_in_russian(tmp_path, monkeypatch):
    class PauseProvider:
        async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
            if not request.tools:
                return LlmProviderResult(
                    success=True,
                    response=LlmProviderResponse(
                        content=json.dumps(
                            {
                                "summary": "Try an apply-like action after observing.",
                                "steps": [
                                    {
                                        "step_id": "apply_click",
                                        "goal": "Click the Apply button.",
                                        "tool_name": "browser.click_by_intent",
                                        "arguments": {"target": "Apply", "role": "button"},
                                        "rationale": "This should require confirmation.",
                                        "requires_confirmation": True,
                                        "is_uncertain": False,
                                    }
                                ],
                                "warnings": [],
                            },
                            ensure_ascii=False,
                        ),
                        finish_reason=LlmFinishReason.STOP,
                        raw_provider_name="mock",
                    ),
                )
            return LlmProviderResult(
                success=True,
                response=LlmProviderResponse(
                    tool_calls=(
                        LlmToolCall(
                            name="browser.click_by_intent",
                            arguments={"target": "Apply", "role": "button"},
                        ),
                    ),
                    finish_reason=LlmFinishReason.TOOL_CALLS,
                    raw_provider_name="mock",
                ),
            )

    monkeypatch.setattr(task_session, "_create_provider", lambda _name, _config: PauseProvider())
    site = prepare_local_interview_site(tmp_path / "site")
    messages: list[str] = []

    with LocalDemoServer(site.root) as server:
        result = asyncio.run(
            run_cli_task(
                CliTaskSettings(
                    task="Нажми Apply, если страница подходит",
                    dry_run=False,
                    report_path=tmp_path / "pause-report.json",
                    replay_path=tmp_path / "pause-replay.json",
                    dashboard="off",
                    provider="mock",
                    start_url=server.url_for(site.start_page_name),
                    max_iterations=2,
                    headless=True,
                ),
                progress=messages.append,
            )
        )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    event_names = [event["name"] for event in report["events"]]
    tool_events = [
        event
        for event in report["events"]
        if event["name"] == "tool_execution_finished"
    ]

    assert result.success is False
    assert "Требуется подтверждение" in result.message_ru
    assert "confirmation_required" in event_names
    assert any("Требуется подтверждение" in message for message in messages)
    assert tool_events[-1]["details"]["tool_status"] == "paused"


def test_live_cli_records_page_blocker_without_raw_html(tmp_path):
    site_root = _write_captcha_blocker_site(tmp_path / "blocker-site")
    messages: list[str] = []

    with LocalDemoServer(site_root) as server:
        result = asyncio.run(
            run_cli_task(
                CliTaskSettings(
                    task="Проверь страницу и остановись, если она заблокирована",
                    dry_run=False,
                    report_path=tmp_path / "blocker-report.json",
                    replay_path=tmp_path / "blocker-replay.json",
                    dashboard="off",
                    provider="mock",
                    start_url=server.url_for("index.html"),
                    max_iterations=2,
                    headless=True,
                ),
                progress=messages.append,
            )
        )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    serialized = json.dumps(report, ensure_ascii=False).casefold()
    blocker_events = [
        event for event in report["events"] if event["name"] == "page_blocker_detected"
    ]

    assert result.success is False
    assert "блокер" in result.message_ru.casefold()
    assert blocker_events
    assert blocker_events[-1]["details"]["blocker_type"] == "captcha_blocking_page"
    assert "captcha_blocking_page" in blocker_events[-1]["details"]["issue_codes"]
    assert any("CAPTCHA" in message or "блокер" in message.casefold() for message in messages)
    assert "<html" not in serialized
    assert "<button" not in serialized
    assert "browser profile" not in serialized


def test_live_cli_interactive_confirmation_resumes_one_action(tmp_path, monkeypatch):
    provider = _ApplyConfirmationProvider()
    monkeypatch.setattr(task_session, "_create_provider", lambda _name, _config: provider)
    monkeypatch.setattr(task_session, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(task_session, "_read_confirmation_answer", lambda _prompt: "да")
    site_root = _write_apply_site(tmp_path / "apply-site")
    messages: list[str] = []

    with LocalDemoServer(site_root) as server:
        result = asyncio.run(
            run_cli_task(
                CliTaskSettings(
                    task="Нажми Apply и продолжи только после подтверждения",
                    dry_run=False,
                    report_path=tmp_path / "approved-report.json",
                    replay_path=tmp_path / "approved-replay.json",
                    dashboard="off",
                    provider="mock",
                    start_url=server.url_for("index.html"),
                    max_iterations=4,
                    headless=True,
                ),
                progress=messages.append,
            )
        )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    event_names = [event["name"] for event in report["events"]]
    click_events = [
        event
        for event in report["events"]
        if event["name"] == "tool_execution_finished"
        and event["details"].get("tool_name") == "browser.click_by_intent"
    ]

    assert result.success is True
    assert "confirmation_required" in event_names
    assert "confirmation_approved" in event_names
    assert any(event["details"]["tool_status"] == "paused" for event in click_events)
    assert any(event["details"]["tool_status"] == "success" for event in click_events)
    assert any("Цель: Apply" in message for message in messages)
    assert any("Подтверждение принято" in message for message in messages)


def test_live_cli_interactive_confirmation_cancel_stops_cleanly(tmp_path, monkeypatch):
    provider = _ApplyConfirmationProvider()
    monkeypatch.setattr(task_session, "_create_provider", lambda _name, _config: provider)
    monkeypatch.setattr(task_session, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(task_session, "_read_confirmation_answer", lambda _prompt: "")
    site_root = _write_apply_site(tmp_path / "apply-site")
    messages: list[str] = []

    with LocalDemoServer(site_root) as server:
        result = asyncio.run(
            run_cli_task(
                CliTaskSettings(
                    task="Нажми Apply только если пользователь подтвердит",
                    dry_run=False,
                    report_path=tmp_path / "cancel-report.json",
                    replay_path=tmp_path / "cancel-replay.json",
                    dashboard="off",
                    provider="mock",
                    start_url=server.url_for("index.html"),
                    max_iterations=3,
                    headless=True,
                ),
                progress=messages.append,
            )
        )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    event_names = [event["name"] for event in report["events"]]
    click_events = [
        event
        for event in report["events"]
        if event["name"] == "tool_execution_finished"
        and event["details"].get("tool_name") == "browser.click_by_intent"
    ]

    assert result.success is False
    assert "Действие отменено пользователем" in result.message_ru
    assert "confirmation_required" in event_names
    assert "task_cancelled" in event_names
    assert "confirmation_approved" not in event_names
    assert any(event["details"]["tool_status"] == "paused" for event in click_events)
    assert not any(event["details"]["tool_status"] == "success" for event in click_events)
    assert any("Как отменить" in message for message in messages)


def test_dashboard_renders_required_status_fields():
    dashboard = RuntimeDashboard(task="Проверить страницу")
    dashboard.render_event(
        RuntimeEvent(
            name="observation_captured",
            status=RuntimeStatus.RUNNING,
            details={
                "state": "observing",
                "summary": "Видна форма поиска и список результатов.",
                "progress": {
                    "iteration": 2,
                    "max_iterations": 4,
                    "completed_steps": 2,
                    "total_steps": 4,
                },
            },
        )
    )
    event = RuntimeEvent(
        name="tool_selected",
        status=RuntimeStatus.RUNNING,
        details={
            "state": "executing",
            "selected_tool": "browser.fill",
            "selected_tool_arguments": {
                "element_id": "field_1",
                "value": "[REDACTED]",
            },
            "current_plan_step": "Заполнить поле поиска; tool: browser.fill",
            "next_action": "skip_execution",
            "progress": {
                "iteration": 2,
                "max_iterations": 4,
                "completed_steps": 2,
                "total_steps": 4,
            },
        },
    )

    output = dashboard.render_event(event)

    assert "Задача: Проверить страницу" in output
    assert "Состояние: подготовка действия" in output
    assert "Итерация: 2/4" in output
    assert "Шаг плана: Заполнить поле поиска; tool: browser.fill" in output
    assert "Краткое наблюдение: Видна форма поиска и список результатов." in output
    assert "Выбранный инструмент: заполнение поля (browser.fill)" in output
    assert 'Аргументы инструмента: {"element_id": "field_1", "value": "[REDACTED]"}' in output
    assert "Решение безопасности: ожидает проверки перед выполнением" in output
    assert "Статус результата: инструмент выбран, выполнение еще не началось" in output
    assert "Прогресс: 2/4 шагов" in output
    assert "Следующее действие: не выполнять действие в сухом запуске" in output


def test_verbose_context_budget_message_shows_compression_evidence():
    message = task_session._event_detail_message(
        RuntimeEvent(
            name="context_budget_applied",
            status=RuntimeStatus.RUNNING,
            details={
                "metrics": {
                    "before_tokens": 1200,
                    "after_tokens": 640,
                    "dialogs_kept": 1,
                    "form_fields_kept": 2,
                    "preserved_critical_facts": 3,
                    "emergency_compression_applied": False,
                }
            },
        ),
        verbose=True,
    )

    assert "Контекст сжат: 1200 -> 640 токенов" in message
    assert "диалоги/формы/важные факты" in message


class _ApplyConfirmationProvider:
    def __init__(self) -> None:
        self.tool_requests = 0

    async def complete(self, request: LlmProviderRequest) -> LlmProviderResult:
        if not request.tools:
            return LlmProviderResult(
                success=True,
                response=LlmProviderResponse(
                    content=json.dumps(
                        {
                            "summary": "Click Apply after explicit confirmation.",
                            "steps": [
                                {
                                    "step_id": "apply_click",
                                    "goal": "Click the Apply button.",
                                    "tool_name": "browser.click_by_intent",
                                    "arguments": {"target": "Apply", "role": "button"},
                                    "rationale": "This action can affect an external system.",
                                    "requires_confirmation": False,
                                    "is_uncertain": False,
                                }
                            ],
                            "warnings": ["Requires user confirmation before clicking Apply."],
                        },
                        ensure_ascii=False,
                    ),
                    finish_reason=LlmFinishReason.STOP,
                    raw_provider_name="mock",
                ),
            )
        self.tool_requests += 1
        if self.tool_requests <= 2:
            return LlmProviderResult(
                success=True,
                response=LlmProviderResponse(
                    tool_calls=(
                        LlmToolCall(
                            name="browser.click_by_intent",
                            arguments={"target": "Apply", "role": "button"},
                        ),
                    ),
                    finish_reason=LlmFinishReason.TOOL_CALLS,
                    raw_provider_name="mock",
                ),
            )
        return LlmProviderResult(
            success=True,
            response=LlmProviderResponse(
                content="Действие выполнено после явного подтверждения.",
                finish_reason=LlmFinishReason.STOP,
                raw_provider_name="mock",
            ),
        )


def _write_apply_site(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Apply confirmation fixture</title>
</head>
<body>
  <main>
    <h1>AI Engineer</h1>
    <p>This deterministic page contains one external-side-effect style action.</p>
    <button type="button" aria-label="Apply" onclick="document.body.dataset.clicked='true'; document.title='Applied locally'">Apply</button>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return root


def _write_captcha_blocker_site(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Human check</title>
</head>
<body>
  <main>
    <h1>Verify you are human</h1>
    <p>CAPTCHA check is required before continuing.</p>
    <button type="button">Continue</button>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return root
