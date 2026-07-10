import asyncio
import json

from scout_pilot.cli.dashboard import RuntimeDashboard
from scout_pilot.cli.task_session import CliTaskSettings, run_cli_task
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
    assert "private-value" not in serialized
    assert "<html" not in serialized
    assert "browser profile" not in serialized


def test_live_cli_mode_fails_clearly_and_writes_report(tmp_path):
    result = asyncio.run(
        run_cli_task(
            CliTaskSettings(
                task="Открой сайт и найди информацию",
                dry_run=False,
                report_path=tmp_path / "report.json",
                replay_path=tmp_path / "replay.json",
                dashboard="off",
            )
        )
    )

    assert result.success is False
    assert "live-режим" in result.message_ru
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["final"]["success"] is False
    assert "dry-run" in report["final"]["failure_ru"]


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
    assert "Состояние: executing" in output
    assert "Выбранный инструмент: browser.observe" in output
    assert "Прогресс: 2/4 шагов" in output
    assert "Следующее действие: skip_execution" in output
