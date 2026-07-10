from scout_pilot.cli.main import main
from scout_pilot.cli.main import build_parser


def test_status_command_prints_russian_placeholder(capsys):
    exit_code = main(["status"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "фундамент проекта готов" in captured.out
    assert "Universal Semantic Navigation" in captured.out
    assert "demo/reporting слой подключены" in captured.out
    assert "Semantic Observation Engine" in captured.out
    assert "scout-pilot run" in captured.out


def test_demo_vacancy_search_command_requires_start_url():
    parser = build_parser()

    args = parser.parse_args(
        [
            "demo-vacancy-search",
            "--start-url",
            "file:///tmp/example.html",
            "--headless",
            "--confirm-search-fill",
        ]
    )

    assert args.command == "demo-vacancy-search"
    assert args.start_url == "file:///tmp/example.html"
    assert args.headless is True
    assert args.confirm_search_fill is True


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
