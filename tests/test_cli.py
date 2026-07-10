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
