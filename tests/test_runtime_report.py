import asyncio
import json
from pathlib import Path

from scout_pilot.models import RuntimeEvent, RuntimeStatus
from scout_pilot.reporting import RuntimeReportRecorder, sanitize_for_report


def test_runtime_report_redacts_sensitive_values_and_raw_html(tmp_path):
    recorder = RuntimeReportRecorder(
        task="Проверить страницу password=hidden",
        mode="test",
        dry_run=True,
    )
    asyncio.run(
        recorder.record(
            RuntimeEvent(
                name="observation_captured",
                status=RuntimeStatus.RUNNING,
                details={
                    "raw_html": "<html><body>secret page</body></html>",
                    "cookie": "session=private",
                    "profile_path": Path("private/profile"),
                    "message": "token=abc123456789",
                    "trace": {
                        "selected_tool": "browser.fill",
                        "tool_arguments": '{"value": "[REDACTED]", "file": "C:\\Users\\Unknown\\Desktop\\secret.txt"}',
                        "observation_summary": "<html><body>raw</body></html>",
                        "security_decision": "token=abc123456789",
                    },
                },
            )
        )
    )
    recorder.finalize(success=True, summary_ru="Готово")
    recorder.write(report_path=tmp_path / "report.json", replay_path=tmp_path / "replay.json")

    serialized = (tmp_path / "report.json").read_text(encoding="utf-8").casefold()

    assert "<html" not in serialized
    assert "secret page" not in serialized
    assert "secret.txt" not in serialized
    assert "private/profile" not in serialized
    assert "abc123456789" not in serialized
    assert "[redacted]" in serialized
    assert "[redacted_path]" in serialized


def test_sanitize_for_report_preserves_safe_structure():
    value = sanitize_for_report(
        {
            "title": "Safe page",
            "nested": [{"token": "private"}, {"count": 2}],
        }
    )

    assert value == {
        "title": "Safe page",
        "nested": [{"token": "[REDACTED]"}, {"count": 2}],
    }


def test_runtime_report_preserves_safe_context_budget_metrics(tmp_path):
    recorder = RuntimeReportRecorder(
        task="Проверить страницу",
        mode="test",
        dry_run=False,
    )
    asyncio.run(
        recorder.record(
            RuntimeEvent(
                name="context_budget_applied",
                status=RuntimeStatus.RUNNING,
                details={
                    "metrics": {
                        "before_tokens": 1800,
                        "after_tokens": 720,
                        "observation_sections_kept": 4,
                        "observation_sections_dropped": 9,
                        "memory_summaries_kept": 5,
                        "memory_summaries_dropped": 18,
                        "emergency_compression_applied": True,
                        "unsafe_note": "<html><body>raw page</body></html>",
                    }
                },
            )
        )
    )
    recorder.finalize(success=True, summary_ru="Готово")
    recorder.write(report_path=tmp_path / "report.json", replay_path=tmp_path / "replay.json")

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    serialized = json.dumps(report, ensure_ascii=False).casefold()
    metrics = report["events"][0]["details"]["metrics"]

    assert metrics["before_tokens"] == 1800
    assert metrics["after_tokens"] == 720
    assert metrics["observation_sections_kept"] == 4
    assert metrics["memory_summaries_dropped"] == 18
    assert "<html" not in serialized
    assert "raw page" not in serialized
