import json

from scout_pilot.cli.main import main
from scout_pilot.reporting import summarize_replay_payload


def test_replay_summary_prints_human_readable_safe_demo_payload():
    summary = summarize_replay_payload(_safe_demo_payload())
    output = "\n".join(summary.lines).casefold()

    assert summary.safe_to_print is True
    assert "сводка отчета/replay" in output
    assert "задача: проверить локальное демо" in output
    assert "итог: успешно; причина: completed" in output
    assert "страницы: 2" in output
    assert "наблюдения: 1" in output
    assert "вызовы инструментов: 2; browser.navigate, browser.click" in output
    assert "паузы безопасности: 1" in output
    assert "контекст: 1 событий; максимум 1200 -> 450 токенов" in output
    assert "итог/заметки:" in output
    assert "<html" not in output
    assert "tok" + "en=" not in output
    assert "cookie" not in output


def test_replay_summary_refuses_unsafe_payload_without_echoing_secret():
    summary = summarize_replay_payload(
        {
            "artifact_kind": "demo_replay",
            "task": "unsafe",
            "events": [
                {
                    "kind": "observation",
                    "details": {
                        "raw_html": "<html><body>secret page</body></html>",
                        "message": "tok" + "en=super-private-value",
                        "profile_path": r"C:\Users\Unknown\Desktop\private",
                    },
                }
            ],
        }
    )
    output = "\n".join(summary.lines)

    assert summary.safe_to_print is False
    assert "не показан как обычная сводка" in output
    assert "небезопасные или неочищенные данные" in output
    assert "<html" not in output.casefold()
    assert "super-private-value" not in output
    assert "C:\\Users\\Unknown" not in output


def test_replay_summary_cli_prints_safe_summary(tmp_path, capsys):
    path = tmp_path / "demo-replay.json"
    path.write_text(json.dumps(_safe_demo_payload(), ensure_ascii=False), encoding="utf-8")

    exit_code = main(["replay-summary", str(path)])

    captured = capsys.readouterr()
    output = captured.out.casefold()
    assert exit_code == 0
    assert "сводка отчета/replay" in output
    assert "проверить локальное демо" in output
    assert "browser.navigate" in output
    assert "<html" not in output
    assert "tok" + "en=" not in output


def test_replay_summary_cli_refuses_unsafe_json(tmp_path, capsys):
    path = tmp_path / "unsafe-replay.json"
    path.write_text(
        json.dumps(
            {
                "artifact_kind": "runtime_replay",
                "task": "unsafe",
                "events": [{"name": "bad", "details": {"cookie": "session=private"}}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exit_code = main(["replay-summary", str(path)])

    captured = capsys.readouterr()
    output = captured.out.casefold()
    assert exit_code == 2
    assert "не показан как обычная сводка" in output
    assert "session=private" not in output
    assert "<html" not in output


def _safe_demo_payload():
    return {
        "schema_version": 1,
        "artifact_kind": "demo_replay",
        "demo_name": "synthetic_food_order",
        "task": "Проверить локальное демо",
        "start_url": "http://127.0.0.1:8080/index.html",
        "events": [
            {
                "kind": "observation",
                "phase": "start",
                "observation": {
                    "url": "http://127.0.0.1:8080/index.html",
                    "title": "Start",
                    "summary": "Safe compact observation.",
                },
            },
            {"kind": "selected_tool", "tool_name": "browser.navigate"},
            {"kind": "selected_tool", "tool_name": "browser.click"},
            {
                "kind": "context_budget",
                "metrics": {
                    "before_tokens": 1200,
                    "after_tokens": 450,
                    "observation_sections_kept": 3,
                    "observation_sections_dropped": 4,
                    "emergency_compression_applied": False,
                },
            },
            {"kind": "tool_result", "status": "success", "success": True},
        ],
        "pages_read": [
            {
                "phase": "start",
                "title": "Start",
                "url": "http://127.0.0.1:8080/index.html",
                "summary": "Safe page.",
            }
        ],
        "security_pauses": [
            {
                "phase": "payment",
                "risk": "external_side_effect",
                "action": "нажать финальную кнопку оплаты",
            }
        ],
        "final_notes": [
            {
                "item_name": "BBQ Burger",
                "reason": "Selected exact synthetic item.",
            }
        ],
        "blockers": [],
        "success": True,
        "stop_reason": "completed",
        "final_summary_ru": "Демо завершено безопасно.",
        "summary": {
            "observation_count": 1,
            "tool_decision_count": 2,
            "security_pause_count": 1,
            "context_budget_events": 1,
            "blocker_count": 0,
        },
    }
