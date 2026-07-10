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
