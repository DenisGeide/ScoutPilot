"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from scout_pilot.config import AppConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout-pilot",
        description="Автономный браузерный агент Scout Pilot.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показать внутренние structured logs уровня INFO.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Показать подробные внутренние structured logs уровня DEBUG.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Показать состояние текущего этапа.")

    run_parser = subparsers.add_parser(
        "run",
        help="Принять одну задачу на естественном языке и показать ход выполнения.",
    )
    run_parser.add_argument("task", nargs="+", help="Текст задачи на естественном языке.")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Безопасный сухой запуск без браузерных действий. Сейчас это режим по умолчанию.",
    )
    run_parser.add_argument(
        "--live",
        action="store_true",
        help="Запросить live-режим. Пока он вернет понятную ошибку и предложит dry-run.",
    )
    run_parser.add_argument("--report-path", help="Куда сохранить JSON-отчет.")
    run_parser.add_argument("--replay-path", help="Куда сохранить JSON replay.")
    run_parser.add_argument(
        "--dashboard",
        choices=("compact", "off"),
        default="compact",
        help="Показывать компактный статус выполнения или только короткие сообщения.",
    )

    interactive_parser = subparsers.add_parser(
        "interactive",
        help="Запустить спокойный интерактивный CLI-режим.",
    )
    interactive_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Безопасный сухой запуск для каждой введенной задачи.",
    )
    interactive_parser.add_argument(
        "--live",
        action="store_true",
        help="Запросить live-режим. Пока он вернет понятную ошибку и предложит dry-run.",
    )
    interactive_parser.add_argument(
        "--report-dir",
        help="Папка для report/replay артефактов. По умолчанию reports/tmp.",
    )
    interactive_parser.add_argument(
        "--dashboard",
        choices=("compact", "off"),
        default="compact",
        help="Показывать компактный статус выполнения или только короткие сообщения.",
    )

    smoke_parser = subparsers.add_parser(
        "browser-smoke",
        help="Запустить локальную проверку браузера.",
    )
    smoke_parser.add_argument("--url", help="Открыть указанный URL после запуска.")
    smoke_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить браузер без видимого окна.",
    )
    smoke_parser.add_argument(
        "--headed",
        action="store_true",
        help="Принудительно запустить видимое окно браузера.",
    )
    smoke_parser.add_argument(
        "--hold-seconds",
        type=float,
        default=2.0,
        help="Сколько секунд держать браузер открытым.",
    )

    demo_parser = subparsers.add_parser(
        "demo-vacancy-search",
        help="Запустить общее демо поиска вакансий с пользовательского URL.",
    )
    demo_parser.add_argument(
        "--start-url",
        required=True,
        help="Начальный URL или домен, переданный пользователем.",
    )
    demo_parser.add_argument(
        "--query",
        default="AI Engineer Python AI Developer",
        help="Поисковый запрос для демо.",
    )
    demo_parser.add_argument(
        "--max-vacancies",
        type=int,
        default=3,
        help="Сколько найденных страниц прочитать.",
    )
    demo_parser.add_argument(
        "--report-path",
        help="Куда сохранить JSON-отчет. По умолчанию используется reports/tmp.",
    )
    demo_parser.add_argument(
        "--replay-path",
        help="Куда сохранить JSON replay. По умолчанию используется reports/tmp.",
    )
    demo_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить браузер без видимого окна.",
    )
    demo_parser.add_argument(
        "--headed",
        action="store_true",
        help="Принудительно запустить видимое окно браузера.",
    )
    demo_parser.add_argument(
        "--confirm-search-fill",
        action="store_true",
        help="Явно подтвердить ввод поискового запроса в поле страницы.",
    )
    demo_parser.add_argument(
        "--confirm-search-submit",
        action="store_true",
        help="Явно подтвердить запуск поиска, если он выглядит как отправка формы.",
    )
    demo_parser.add_argument(
        "--probe-security",
        action="store_true",
        help="Проверить, что отклик/сообщение останавливается на подтверждении.",
    )

    interview_parser = subparsers.add_parser(
        "interview-demo",
        help="Запустить локальное interview demo на тестовых страницах.",
    )
    interview_parser.add_argument(
        "--query",
        default="AI Engineer Python AI Developer",
        help="Поисковый запрос для локального демо.",
    )
    interview_parser.add_argument(
        "--max-vacancies",
        type=int,
        default=3,
        help="Сколько найденных страниц прочитать.",
    )
    interview_parser.add_argument(
        "--site-dir",
        help="Куда сгенерировать тестовый сайт. По умолчанию reports/tmp/interview-demo-site.",
    )
    interview_parser.add_argument(
        "--profile-dir",
        help="Persistent browser profile для демо. По умолчанию .browser-profiles/interview-demo.",
    )
    interview_parser.add_argument(
        "--report-path",
        help="Куда сохранить JSON-отчет. По умолчанию reports/tmp/interview-demo-report.json.",
    )
    interview_parser.add_argument(
        "--replay-path",
        help="Куда сохранить JSON replay. По умолчанию reports/tmp/interview-demo-replay.json.",
    )
    interview_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить локальное demo без видимого окна.",
    )
    interview_parser.add_argument(
        "--headed",
        action="store_true",
        help="Принудительно запустить видимое окно браузера. Это режим по умолчанию.",
    )
    interview_parser.add_argument(
        "--slow-mo-ms",
        type=int,
        default=80,
        help="Небольшая задержка browser actions для записи видео.",
    )
    interview_parser.add_argument(
        "--wait-after-search-ms",
        type=int,
        default=200,
        help="Сколько миллисекунд ждать после запуска поиска на тестовой странице.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(verbose=args.verbose, debug=args.debug)

    if args.command in (None, "status"):
        config = AppConfig.load()
        _print_status(config)
        return 0

    if args.command == "run":
        return asyncio.run(_run_task(args))

    if args.command == "interactive":
        return asyncio.run(_run_interactive(args))

    if args.command == "browser-smoke":
        return asyncio.run(_run_browser_smoke(args))

    if args.command == "demo-vacancy-search":
        return asyncio.run(_run_vacancy_demo(args))

    if args.command == "interview-demo":
        return asyncio.run(_run_interview_demo(args))

    parser.print_help()
    return 0


def _print_status(config: AppConfig) -> None:
    print("Scout Pilot: проект установлен, основные слои доступны.")
    print(
        "Browser Engine, Semantic Observation Engine, Tool Runtime, LLM Provider Layer, "
        "Planning Engine, Hierarchical Memory, Autonomous Agent Runtime, Execution "
        "Intelligence, Context Budgeting, Security Policy, Universal Semantic Navigation "
        "и demo/reporting слой подключены."
    )
    print(
        "Демо поиска вакансий запускается командой demo-vacancy-search с URL, который "
        "передает пользователь. Live LLM-вызовы из CLI пока не включены."
    )
    print("Локальное interview demo доступно через scout-pilot interview-demo.")
    print("Одну задачу можно запустить через scout-pilot run \"текст задачи\" --dry-run.")
    print("Интерактивный режим доступен через scout-pilot interactive.")
    print(f"Среда: {config.environment}. Профиль браузера: {config.browser_profile_dir}.")
    print(f"LLM-провайдер: {config.llm_provider}. Модель: {config.llm_model}.")
    mode = "без видимого окна" if config.browser_headless else "с видимым окном"
    print(f"Режим браузера по умолчанию: {mode}.")


async def _run_task(args: argparse.Namespace) -> int:
    from scout_pilot.cli.task_session import (
        CliTaskSettings,
        default_artifact_paths,
        run_cli_task,
    )

    config = AppConfig.load()
    task_text = " ".join(args.task).strip()
    default_paths = default_artifact_paths(config.reports_dir / "tmp")
    settings = CliTaskSettings(
        task=task_text,
        dry_run=not args.live,
        report_path=Path(args.report_path) if args.report_path else default_paths.report_path,
        replay_path=Path(args.replay_path) if args.replay_path else default_paths.replay_path,
        dashboard=args.dashboard,
    )
    result = await run_cli_task(settings, progress=print)
    if result.success:
        return 0
    print("Что можно сделать дальше: запустите с --dry-run или используйте demo-vacancy-search.")
    return 1


async def _run_interactive(args: argparse.Namespace) -> int:
    from scout_pilot.cli.task_session import (
        CliTaskSettings,
        default_artifact_paths,
        run_cli_task,
    )

    config = AppConfig.load()
    report_dir = Path(args.report_dir) if args.report_dir else config.reports_dir / "tmp"
    print("Интерактивный режим Scout Pilot.")
    print("Введите задачу и нажмите Enter. Для выхода введите пустую строку или `выход`.")
    while True:
        try:
            task_text = input("Задача> ").strip()
        except EOFError:
            print("Ввод завершен.")
            return 0
        if not task_text or task_text.casefold() in {"exit", "quit", "выход"}:
            print("Интерактивный режим завершен.")
            return 0

        default_paths = default_artifact_paths(report_dir, prefix="interactive")
        settings = CliTaskSettings(
            task=task_text,
            dry_run=not args.live,
            report_path=default_paths.report_path,
            replay_path=default_paths.replay_path,
            dashboard=args.dashboard,
        )
        result = await run_cli_task(settings, progress=print)
        if not result.success:
            print("Задача не была выполнена. Можно ввести новую задачу или выйти.")


async def _run_browser_smoke(args: argparse.Namespace) -> int:
    from scout_pilot.browser import (
        BrowserEngineConfig,
        BrowserEngineError,
        PlaywrightBrowserEngine,
    )

    config = AppConfig.load()
    settings = BrowserEngineConfig.from_app_config(config)
    if args.headless:
        settings = replace(settings, headless=True)
    if args.headed:
        settings = replace(settings, headless=False)

    engine = PlaywrightBrowserEngine(settings)
    print("Запускаю браузер...")
    try:
        session = await engine.start()
        print(f"Браузер запущен. Сессия: {session.session_id}.")
        if args.url:
            result = await engine.navigate_to(args.url)
            if not result.success:
                print(f"Не удалось открыть страницу: {result.message}")
                return 1
            print(f"Страница открыта: {result.title or result.url or args.url}")
        await asyncio.sleep(max(args.hold_seconds, 0))
        return 0
    except BrowserEngineError as exc:
        print(f"Не удалось запустить браузер: {exc}")
        return 1
    finally:
        await engine.stop()
        print("Браузер закрыт.")


async def _run_vacancy_demo(args: argparse.Namespace) -> int:
    from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
    from scout_pilot.demo import VacancySearchDemoRunner, VacancySearchDemoSettings
    from scout_pilot.observation import ObservationSettings, SemanticObservationEngine
    from scout_pilot.tools import DefaultToolRuntime, ToolContext, create_browser_tool_registry

    config = AppConfig.load()
    browser_settings = BrowserEngineConfig.from_app_config(config)
    if args.headless:
        browser_settings = replace(browser_settings, headless=True)
    if args.headed:
        browser_settings = replace(browser_settings, headless=False)

    report_path = (
        Path(args.report_path)
        if args.report_path
        else config.reports_dir / "tmp" / "demo-vacancy-search-report.json"
    )
    replay_path = (
        Path(args.replay_path)
        if args.replay_path
        else config.reports_dir / "tmp" / "demo-vacancy-search-replay.json"
    )
    demo_settings = VacancySearchDemoSettings(
        start_url=args.start_url,
        query=args.query,
        max_vacancies=args.max_vacancies,
        report_path=report_path,
        replay_path=replay_path,
        confirm_search_fill=args.confirm_search_fill,
        confirm_search_submit=args.confirm_search_submit,
        probe_security=args.probe_security,
    )

    browser = PlaywrightBrowserEngine(browser_settings)
    observation_engine = SemanticObservationEngine(
        browser,
        ObservationSettings.from_app_config(config),
    )
    tool_runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(browser=browser, observation_engine=observation_engine),
    )
    runner = VacancySearchDemoRunner(
        browser=browser,
        observation_engine=observation_engine,
        tool_runtime=tool_runtime,
    )

    result = await runner.run(demo_settings, progress=print)
    print(result.message_ru)
    print(f"Отчет сохранен: {result.report_path}")
    if result.replay_path is not None:
        print(f"Replay сохранен: {result.replay_path}")
    if result.success:
        return 0
    if result.stop_reason == "confirmation_required":
        return 2
    return 1


async def _run_interview_demo(args: argparse.Namespace) -> int:
    from scout_pilot.demo import InterviewDemoSettings, run_local_interview_demo

    config = AppConfig.load()
    settings = InterviewDemoSettings(
        site_dir=Path(args.site_dir) if args.site_dir else Path("reports/tmp/interview-demo-site"),
        profile_dir=(
            Path(args.profile_dir)
            if args.profile_dir
            else Path(".browser-profiles/interview-demo")
        ),
        report_path=(
            Path(args.report_path)
            if args.report_path
            else Path("reports/tmp/interview-demo-report.json")
        ),
        replay_path=(
            Path(args.replay_path)
            if args.replay_path
            else Path("reports/tmp/interview-demo-replay.json")
        ),
        query=args.query,
        max_vacancies=args.max_vacancies,
        headless=True if args.headless else False,
        slow_mo_ms=args.slow_mo_ms,
        wait_after_search_ms=args.wait_after_search_ms,
    )
    if args.headed:
        settings = replace(settings, headless=False)

    result = await run_local_interview_demo(config, settings, progress=print)
    print(result.message_ru)
    print(f"Отчет сохранен: {result.report_path}")
    print(f"Replay сохранен: {result.replay_path}")
    print(f"Прочитано страниц: {result.notes_count}. Пауз безопасности: {result.security_pause_count}.")
    if result.success:
        print("Локальное interview demo завершено. Реальные отклики и сообщения не отправлялись.")
        return 0
    print("Демо остановилось. Проверьте отчет и replay, чтобы увидеть причину.")
    return 1


def _configure_logging(*, verbose: bool, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_StructuredLogFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


class _StructuredLogFormatter(logging.Formatter):
    """Emit compact JSON logs for internal diagnostics."""

    _EXTRA_KEYS = (
        "event",
        "task_id",
        "state",
        "status",
        "tool_name",
        "dry_run",
        "error_code",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in self._EXTRA_KEYS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)
