import asyncio
import json
import importlib
import logging
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from scout_pilot.cli.main import main
from scout_pilot.cli.main import build_parser
from scout_pilot.cli.main import _extract_terminal_links
from scout_pilot.cli.main import _extract_selected_result_reference
from scout_pilot.cli.main import _ChatStatusLine
from scout_pilot.cli.main import _chat_turn_memory_summaries
from scout_pilot.cli.main import _ensure_menu_browser_available
from scout_pilot.cli.main import _format_chat_line
from scout_pilot.cli.main import _format_chat_prompt
from scout_pilot.cli.main import _format_terminal_links
from scout_pilot.cli.main import _normalize_menu_start_url
from scout_pilot.cli.main import _open_menu_link
from scout_pilot.cli.main import _open_previous_selected_result
from scout_pilot.cli.main import _parse_open_link_command
from scout_pilot.cli.main import _print_chat_run_evidence
from scout_pilot.cli.main import _StructuredLogFormatter
from scout_pilot.cli.main import _task_references_previous_selection
from scout_pilot.models import ToolRequest


def test_status_command_prints_russian_placeholder(capsys):
    exit_code = main(["status"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "проект установлен, основные слои доступны" in captured.out
    assert "Universal Semantic Navigation" in captured.out
    assert "слой demo/reporting" in captured.out
    assert "Semantic Observation Engine" in captured.out
    assert "scout-pilot run" in captured.out


def test_menu_command_parses_launcher_defaults():
    parser = build_parser()

    args = parser.parse_args(["menu"])

    assert args.command == "menu"
    assert args.provider == "codex"
    assert args.dashboard == "off"
    assert args.max_iterations == 128
    assert args.headless is False


def test_menu_opens_and_exits_without_starting_browser(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "9")

    exit_code = main(["menu"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Scout Pilot - меню запуска" in captured.out
    assert "0 - Чат с агентом" in captured.out
    assert "Меню закрыто" in captured.out


def test_menu_chat_mode_collects_url_without_starting_real_browser(monkeypatch, capsys):
    cli_main = importlib.import_module("scout_pilot.cli.main")
    answers = iter(["0", "hh.ru", "9"])
    calls = []

    async def fake_chat_session(args, *, start_url, provider):
        calls.append((start_url, provider))
        return 0

    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(cli_main, "_menu_chat_session", fake_chat_session)

    exit_code = main(["menu"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Чат с агентом" in captured.out
    assert calls == [("https://hh.ru", "codex")]


def test_menu_chat_hides_repeated_observation_noise_and_uses_honest_wait_text():
    cli_main = importlib.import_module("scout_pilot.cli.main")
    observation = SimpleNamespace(
        name="observation_captured",
        details={"title": "Search results"},
    )

    assert cli_main._menu_chat_event_message(observation, debug_output=False) == ""
    assert "Search results" in cli_main._menu_chat_event_message(
        observation,
        debug_output=True,
    )
    assert cli_main._menu_tool_action_ru("browser.wait") == "жду завершения загрузки данных..."
    assert cli_main._menu_tool_done_ru("browser.wait") == "ожидание завершено."


def test_menu_chat_reports_exact_page_blocker_in_russian():
    cli_main = importlib.import_module("scout_pilot.cli.main")
    login_wall = SimpleNamespace(
        name="page_blocker_detected",
        details={"stop": True, "blocker_type": "login_wall"},
    )
    captcha = SimpleNamespace(
        name="page_blocker_detected",
        details={"stop": True, "blocker_type": "captcha_blocking_page"},
    )

    login_message = cli_main._menu_chat_event_message(login_wall, debug_output=False)
    captcha_message = cli_main._menu_chat_event_message(captcha, debug_output=False)

    assert "ручного входа" in login_message
    assert "CAPTCHA" not in login_message
    assert "CAPTCHA" in captcha_message


def test_menu_url_normalization_accepts_plain_domain():
    normalized, error = _normalize_menu_start_url("hh.ru")

    assert normalized == "https://hh.ru"
    assert error is None


def test_menu_url_normalization_rejects_prompt_text():
    normalized, error = _normalize_menu_start_url("Стартовый URL:")

    assert normalized is None
    assert "настоящий URL" in str(error)


def test_terminal_links_are_short_clickable_and_blue_when_enabled():
    message = "Вакансия: https://hh.ru/vacancy/134165467?query=AI+Engineer&source=search."

    rendered = _format_terminal_links(message, use_color=True)

    assert "\x1b[94mhttps://hh.ru/vacancy/134165467\x1b[0m" in rendered
    assert "\x1b]8;;https://hh.ru/vacancy/134165467?query=AI+Engineer&source=search" in rendered
    assert "[1]" in rendered
    assert "Ctrl + клик или /open 1" in rendered
    assert rendered.endswith(".")


def test_chat_roles_have_distinct_terminal_colors_when_enabled():
    agent = _format_chat_line("Агент", "страница открыта.", use_color=True)
    user = _format_chat_prompt("Вы > ", use_color=True)

    assert "\x1b[96mАгент:" in agent
    assert agent.endswith("страница открыта.")
    assert "\x1b[93mВы > " in user
    assert agent.endswith("\x1b[0m") is False
    assert user.endswith("\x1b[0m")


def test_chat_roles_remain_plain_when_color_is_disabled():
    assert (
        _format_chat_line("Агент", "страница открыта.", use_color=False)
        == "Агент: страница открыта."
    )
    assert _format_chat_prompt("Вы > ", use_color=False) == "Вы > "


def test_chat_status_replaces_the_current_terminal_line():
    class TtyBuffer(StringIO):
        def isatty(self):
            return True

    stream = TtyBuffer()
    status = _ChatStatusLine(stream, use_color=False)

    status.update("ищу вакансии...")
    status.update("читаю вторую вакансию...")
    status.clear()

    assert stream.getvalue() == (
        "\r\x1b[2KАгент: ищу вакансии...\r\x1b[2KАгент: читаю вторую вакансию...\r\x1b[2K"
    )
    assert status.last_message == "читаю вторую вакансию..."


def test_chat_status_stays_silent_when_output_is_redirected():
    stream = StringIO()
    status = _ChatStatusLine(stream, use_color=False)

    status.update("открываю страницу...")
    status.clear()

    assert stream.getvalue() == ""
    assert status.last_message == "открываю страницу..."


def test_terminal_links_keep_plain_output_readable_without_color():
    rendered = _format_terminal_links(
        "Ссылка: https://example.test/jobs/ai-engineer?tracking=private",
        use_color=False,
    )

    assert rendered == (
        "Ссылка: [1] https://example.test/jobs/ai-engineer  [Ctrl + клик или /open 1]"
    )
    assert "\x1b[" not in rendered


def test_terminal_links_keep_exact_targets_and_stable_numbers():
    first = "https://example.test/items/1001?source=search"
    second = "https://example.test/items/1002?source=search"
    message = f"Первый: {first}. Повтор: {first}. Второй: {second}."

    rendered = _format_terminal_links(message, use_color=False)

    assert _extract_terminal_links(message) == (first, second)
    assert rendered.count("[1] https://example.test/items/1001") == 2
    assert rendered.count("[2] https://example.test/items/1002") == 1


def test_open_link_command_validates_available_number():
    assert _parse_open_link_command("/open 2", 3) == (2, None)
    assert "например /open 1" in str(_parse_open_link_command("/open", 3)[1])
    assert "пока нет ссылок" in str(_parse_open_link_command("/open 1", 0)[1])
    assert "Доступны номера" in str(_parse_open_link_command("/open 4", 3)[1])


def test_open_menu_link_uses_exact_url_through_tool_runtime():
    target_url = "https://example.test/items/1001?source=search"

    class FakeRuntime:
        def __init__(self):
            self.requests = []

        async def execute(self, request):
            self.requests.append(request)
            return SimpleNamespace(success=True, message="Navigation completed.")

    class FakeBrowser:
        async def current_state(self):
            return SimpleNamespace(title="AI Engineer vacancy")

    runtime = FakeRuntime()
    message = asyncio.run(
        _open_menu_link(
            link_index=1,
            target_url=target_url,
            tool_runtime=runtime,
            browser=FakeBrowser(),
        )
    )

    assert runtime.requests == [ToolRequest("browser.navigate", {"url": target_url})]
    assert message == "Открыл ссылку 1: AI Engineer vacancy"


def test_follow_up_opens_the_exact_result_selected_in_previous_answer():
    selected_url = "https://example.test/vacancies/1002?source=search"
    answer = (
        "1. Python AI Developer\n"
        "https://example.test/vacancies/1001?source=search\n\n"
        "2. ML/LLM Engineer в AI Lab\n"
        f"{selected_url}\n\n"
        "3. AI Platform Engineer\n"
        "https://example.test/vacancies/1003?source=search\n\n"
        "Лучший вариант — ML/LLM Engineer в AI Lab: наиболее точное совпадение."
    )

    selected = _extract_selected_result_reference(answer)

    assert selected == ("ML/LLM Engineer в AI Lab", selected_url)
    assert _task_references_previous_selection("Открой лучшую вакансию и покажи её")
    assert any(selected_url in summary for summary in _chat_turn_memory_summaries("Сравни", answer))

    class FakeRuntime:
        def __init__(self):
            self.requests = []

        async def execute(self, request):
            self.requests.append(request)
            return SimpleNamespace(success=True, message="Navigation completed.")

    class FakeBrowser:
        async def current_state(self):
            return SimpleNamespace(
                url="https://example.test/vacancies/1003?source=search",
                title="AI Platform Engineer",
            )

    runtime = FakeRuntime()
    message = asyncio.run(
        _open_previous_selected_result(
            label=selected[0],
            target_url=selected[1],
            tool_runtime=runtime,
            browser=FakeBrowser(),
        )
    )

    assert runtime.requests == [ToolRequest("browser.navigate", {"url": selected_url})]
    assert "ML/LLM Engineer в AI Lab" in message


def test_chat_run_evidence_prints_context_and_distinct_page_counts(capsys):
    runtime = SimpleNamespace(
        last_observed_resource_urls=(
            "https://example.test/vacancies/1001",
            "https://example.test/vacancies/1002",
            "https://example.test/vacancies/1003",
        ),
        last_repeated_target_preventions=2,
    )

    _print_chat_run_evidence(
        runtime,
        {
            "before_tokens": 5200,
            "after_tokens": 1800,
            "observation_sections_kept": 5,
            "observation_sections_before": 12,
            "memory_summaries_kept": 3,
            "memory_summaries_before": 6,
            "emergency_compression_applied": False,
        },
    )

    output = capsys.readouterr().out
    assert "Контекст: 5200 -> 1800 токенов" in output
    assert "разделы 5/12" in output
    assert "Страницы в контексте задачи: 3" in output
    assert "повторных переходов предотвращено 2" in output


def test_menu_restarts_disconnected_browser_before_next_task():
    start_url = "https://example.test/search"

    class FakeBrowser:
        def __init__(self):
            self.stopped = 0
            self.started = 0

        async def current_state(self):
            return SimpleNamespace(is_started=False)

        async def stop(self):
            self.stopped += 1

        async def start(self):
            self.started += 1

    class FakeRuntime:
        def __init__(self):
            self.requests = []

        async def execute(self, request):
            self.requests.append(request)
            return SimpleNamespace(success=True)

    browser = FakeBrowser()
    runtime = FakeRuntime()

    ready, message = asyncio.run(
        _ensure_menu_browser_available(
            browser=browser,
            tool_runtime=runtime,
            start_url=start_url,
        )
    )

    assert ready is True
    assert "восстановлена" in str(message)
    assert browser.stopped == 1
    assert browser.started == 1
    assert runtime.requests == [ToolRequest("browser.navigate", {"url": start_url})]


def test_structured_log_hides_traceback_outside_debug_mode():
    try:
        raise RuntimeError("private driver details")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="scout_pilot.runtime.agent",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="runtime_fatal_error",
        args=(),
        exc_info=exc_info,
    )
    rendered = _StructuredLogFormatter(include_traceback=False).format(record)

    assert '"exception_type": "RuntimeError"' in rendered
    assert "private driver details" not in rendered
    assert "Traceback" not in rendered


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


def test_run_accepts_user_facing_max_actions_alias():
    parser = build_parser()

    args = parser.parse_args(["run", "Проверить", "--live", "--max-actions", "30"])

    assert args.max_iterations == 30


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


def test_doctor_command_accepts_optional_provider_choice():
    parser = build_parser()

    args = parser.parse_args(["doctor", "--provider", "anthropic"])

    assert args.command == "doctor"
    assert args.provider == "anthropic"


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
    provider_source = (
        (source_root / "llm" / "mock_provider.py").read_text(encoding="utf-8").casefold()
    )
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
