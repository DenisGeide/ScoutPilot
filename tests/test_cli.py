from scout_pilot.cli.main import main


def test_status_command_prints_russian_placeholder(capsys):
    exit_code = main(["status"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "фундамент проекта готов" in captured.out
    assert "Planning Engine подключены" in captured.out
    assert "Semantic Observation Engine" in captured.out
