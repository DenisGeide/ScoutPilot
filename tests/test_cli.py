import json

from scout_pilot.cli.main import main
from scout_pilot.cli.main import build_parser


def test_status_command_prints_russian_placeholder(capsys):
    exit_code = main(["status"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "проект установлен, основные слои доступны" in captured.out
    assert "Universal Semantic Navigation" in captured.out
    assert "слой demo/reporting" in captured.out
    assert "Semantic Observation Engine" in captured.out
    assert "scout-pilot run" in captured.out


def test_demo_vacancy_search_command_requires_start_url():
    parser = build_parser()

    args = parser.parse_args(
        [
            "demo-vacancy-search",
            "--start-url",
            "https://example.test",
            "--headless",
            "--confirm-search-fill",
        ]
    )

    assert args.command == "demo-vacancy-search"
    assert args.start_url == "https://example.test"
    assert args.headless is True
    assert args.confirm_search_fill is True


def test_interview_demo_command_parses_local_mode():
    parser = build_parser()

    args = parser.parse_args(
        [
            "interview-demo",
            "--headless",
            "--slow-mo-ms",
            "0",
            "--wait-after-search-ms",
            "50",
        ]
    )

    assert args.command == "interview-demo"
    assert args.headless is True
    assert args.slow_mo_ms == 0
    assert args.wait_after_search_ms == 50


def test_run_command_accepts_natural_language_task():
    parser = build_parser()

    args = parser.parse_args(
        [
            "run",
            "Найди",
            "подходящие",
            "вакансии",
            "--dry-run",
            "--dashboard",
            "off",
        ]
    )

    assert args.command == "run"
    assert " ".join(args.task) == "Найди подходящие вакансии"
    assert args.dry_run is True
    assert args.dashboard == "off"


def test_interactive_command_parses_dry_run_mode():
    parser = build_parser()

    args = parser.parse_args(["interactive", "--dry-run", "--dashboard", "compact"])

    assert args.command == "interactive"
    assert args.dry_run is True
    assert args.dashboard == "compact"


def test_run_command_starts_and_writes_artifacts(tmp_path, capsys):
    report_path = tmp_path / "report.json"
    replay_path = tmp_path / "replay.json"

    exit_code = main(
        [
            "run",
            "Проверь",
            "страницу",
            "--dry-run",
            "--dashboard",
            "off",
            "--report-path",
            str(report_path),
            "--replay-path",
            str(replay_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Сухой запуск завершен" in captured.out
    assert report_path.exists()
    assert replay_path.exists()


def test_interview_demo_runs_local_synthetic_site(tmp_path, capsys):
    report_path = tmp_path / "interview-report.json"
    replay_path = tmp_path / "interview-replay.json"

    exit_code = main(
        [
            "interview-demo",
            "--headless",
            "--slow-mo-ms",
            "0",
            "--wait-after-search-ms",
            "50",
            "--site-dir",
            str(tmp_path / "site"),
            "--profile-dir",
            str(tmp_path / "profile"),
            "--report-path",
            str(report_path),
            "--replay-path",
            str(replay_path),
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    serialized = json.dumps(report, ensure_ascii=False).casefold()

    assert exit_code == 0
    assert "Локальное демо для интервью завершено" in captured.out
    assert report["success"] is True
    assert report["summary"]["observation_count"] >= 1
    assert report["summary"]["tool_decision_count"] >= 1
    assert report["summary"]["context_budget_events"] >= 1
    assert report["summary"]["security_pause_count"] >= 1
    assert replay["artifact_kind"] == "demo_replay"
    assert "<html" not in serialized
    assert "<button" not in serialized
    assert "token=" not in serialized
    assert "cookie" not in serialized
