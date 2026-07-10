import asyncio
import json

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
                task="Найди вакансии token=private-value",
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
    assert "браузерные действия" in serialized
    assert "private-value" not in serialized
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


def test_dashboard_renders_required_status_fields():
    dashboard = RuntimeDashboard(task="Проверить страницу")
    event = RuntimeEvent(
        name="tool_selected",
        status=RuntimeStatus.RUNNING,
        details={
            "state": "executing",
            "selected_tool": "browser.observe",
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
    assert "Выбранный инструмент: наблюдение страницы (browser.observe)" in output
    assert "Прогресс: 2/4 шагов" in output
    assert "Следующее действие: не выполнять действие в сухом запуске" in output
