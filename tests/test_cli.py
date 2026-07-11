import json
from pathlib import Path

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


def test_live_local_demo_command_parses_runtime_mode():
    parser = build_parser()

    args = parser.parse_args(
        [
            "live-local-demo",
            "Найди",
            "вакансии",
            "--provider",
            "mock",
            "--headless",
            "--max-iterations",
            "8",
            "--dashboard",
            "verbose",
        ]
    )

    assert args.command == "live-local-demo"
    assert " ".join(args.task) == "Найди вакансии"
    assert args.provider == "mock"
    assert args.headless is True
    assert args.max_iterations == 8
    assert args.dashboard == "verbose"


def test_mail_spam_demo_command_parses_local_mode():
    parser = build_parser()

    args = parser.parse_args(
        [
            "mail-spam-demo",
            "--headless",
            "--slow-mo-ms",
            "0",
            "--site-dir",
            "site",
            "--profile-dir",
            "profile",
        ]
    )

    assert args.command == "mail-spam-demo"
    assert args.headless is True
    assert args.slow_mo_ms == 0
    assert args.site_dir == "site"
    assert args.profile_dir == "profile"


def test_food_order_demo_command_parses_local_mode():
    parser = build_parser()

    args = parser.parse_args(
        [
            "food-order-demo",
            "--headless",
            "--slow-mo-ms",
            "0",
            "--site-dir",
            "site",
            "--profile-dir",
            "profile",
        ]
    )

    assert args.command == "food-order-demo"
    assert args.headless is True
    assert args.slow_mo_ms == 0
    assert args.site_dir == "site"
    assert args.profile_dir == "profile"


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


def test_provider_smoke_command_accepts_provider_choice():
    parser = build_parser()

    args = parser.parse_args(["provider-smoke", "--provider", "openai"])

    assert args.command == "provider-smoke"
    assert args.provider == "openai"


def test_profile_info_command_accepts_profile_name():
    parser = build_parser()

    args = parser.parse_args(["profile-info", "--profile", "default"])

    assert args.command == "profile-info"
    assert args.profile == "default"


def test_profile_open_command_accepts_start_url_and_headed_mode():
    parser = build_parser()

    args = parser.parse_args(
        [
            "profile-open",
            "--profile",
            "default",
            "--start-url",
            "https://example.test",
            "--headed",
            "--hold-seconds",
            "0",
        ]
    )

    assert args.command == "profile-open"
    assert args.profile == "default"
    assert args.start_url == "https://example.test"
    assert args.headed is True
    assert args.hold_seconds == 0


def test_profile_info_command_prints_git_ignore_status(capsys):
    exit_code = main(["profile-info"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Профиль: default" in captured.out
    assert "Путь профиля:" in captured.out
    assert "Игнорируется Git: да" in captured.out
    assert "Не коммитьте browser profile" in captured.out


def test_provider_smoke_without_key_exits_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = main(["provider-smoke", "--provider", "openai"])

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "API-ключ" in captured.out
    assert ".env" in captured.out


def test_run_live_command_accepts_provider_start_url_and_limits():
    parser = build_parser()

    args = parser.parse_args(
        [
            "run",
            "Найди",
            "страницу",
            "--live",
            "--provider",
            "mock",
            "--start-url",
            "https://example.test",
            "--max-iterations",
            "3",
            "--headless",
            "--dashboard",
            "verbose",
        ]
    )

    assert args.command == "run"
    assert args.live is True
    assert args.provider == "mock"
    assert args.start_url == "https://example.test"
    assert args.max_iterations == 3
    assert args.headless is True
    assert args.dashboard == "verbose"


def test_run_live_command_accepts_explicit_headed_flag():
    parser = build_parser()

    args = parser.parse_args(["run", "Проверить", "--live", "--headed"])

    assert args.live is True
    assert args.headed is True


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


def test_live_local_demo_runs_through_autonomous_runtime(tmp_path, capsys):
    report_path = tmp_path / "live-local-report.json"
    replay_path = tmp_path / "live-local-replay.json"

    exit_code = main(
        [
            "live-local-demo",
            "--headless",
            "--slow-mo-ms",
            "0",
            "--site-dir",
            str(tmp_path / "site"),
            "--profile-dir",
            str(tmp_path / "profile"),
            "--report-path",
            str(report_path),
            "--replay-path",
            str(replay_path),
            "--dashboard",
            "off",
            "--max-iterations",
            "8",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    serialized = json.dumps(report, ensure_ascii=False).casefold()
    event_names = [event["name"] for event in report["events"]]
    selected_tools = [
        event["details"].get("selected_tool")
        for event in report["events"]
        if event["name"] == "tool_selected"
    ]
    search_page_titles = {"Local AI Roles", "Local AI Role Matches"}
    detail_titles = {
        event["details"].get("title")
        for event in report["events"]
        if event["name"] in {"observation_captured", "post_action_observation_captured"}
        and event["details"].get("title")
        and event["details"].get("title") not in search_page_titles
    }

    assert exit_code == 0
    assert "ожидаемая безопасная остановка" in captured.out
    assert report["artifact_kind"] == "runtime_report"
    assert replay["artifact_kind"] == "runtime_replay"
    assert report["dry_run"] is False
    assert "tool_execution_finished" in event_names
    assert "confirmation_required" in event_names
    assert "browser.click_by_intent" in selected_tools
    assert "browser.resolve_target" in selected_tools
    assert "browser.navigate" in selected_tools
    assert len(detail_titles) == 3
    assert any(
        event["details"].get("tool_status") == "paused"
        for event in report["events"]
        if event["name"] == "tool_execution_finished"
    )
    assert "сравнение требований подготовлено" in serialized
    assert "<html" not in serialized
    assert "<button" not in serialized
    assert "cookie" not in serialized
    assert "token=" not in serialized


def test_mail_spam_demo_runs_local_synthetic_site(tmp_path, capsys):
    report_path = tmp_path / "mail-report.json"
    replay_path = tmp_path / "mail-replay.json"

    exit_code = main(
        [
            "mail-spam-demo",
            "--headless",
            "--slow-mo-ms",
            "0",
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
    serialized = json.dumps(report, ensure_ascii=False).casefold()

    assert exit_code == 0
    assert "Почтовое demo завершено безопасно" in captured.out
    assert report["success"] is True
    assert report["demo_name"] == "synthetic_mail_spam"
    assert report["summary"]["pages_read_count"] == 10
    assert report["summary"]["security_pause_count"] >= 1
    assert replay_path.exists()
    assert "<html" not in serialized
    assert "<button" not in serialized
    assert "cookie" not in serialized


def test_food_order_demo_runs_local_synthetic_site(tmp_path, capsys):
    report_path = tmp_path / "food-report.json"
    replay_path = tmp_path / "food-replay.json"

    exit_code = main(
        [
            "food-order-demo",
            "--headless",
            "--slow-mo-ms",
            "0",
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
    serialized = json.dumps(report, ensure_ascii=False).casefold()

    assert exit_code == 0
    assert "Food-order demo завершено безопасно" in captured.out
    assert report["success"] is True
    assert report["demo_name"] == "synthetic_food_order"
    assert report["summary"]["pages_read_count"] >= 3
    assert report["summary"]["security_pause_count"] >= 1
    assert replay_path.exists()
    assert "bbq burger" in serialized
    assert "french fries" in serialized
    assert "<html" not in serialized
    assert "<button" not in serialized
    assert "cookie" not in serialized


def test_live_local_demo_provider_uses_no_site_routes_or_selectors():
    source_root = Path(__file__).resolve().parents[1] / "src" / "scout_pilot"
    provider_source = (source_root / "llm" / "mock_provider.py").read_text(
        encoding="utf-8"
    ).casefold()
    class_source = provider_source.split("class deterministiclocaldemomockprovider", 1)[1]
    class_source = class_source.split("def _local_demo_plan_response", 1)[0]
    forbidden = (
        "detail-alpha",
        "detail-beta",
        "detail-gamma",
        ".html",
        "queryselector",
        "locator(",
        "xpath",
    )

    for term in forbidden:
        assert term not in class_source
