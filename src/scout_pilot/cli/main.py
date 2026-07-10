"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from scout_pilot.config import AppConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout-pilot",
        description="Автономный браузерный агент Scout Pilot.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Показать состояние текущего этапа.")

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
        help="Запустить generic-демо поиска вакансий с пользовательского URL.",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in (None, "status"):
        config = AppConfig.load()
        _print_status(config)
        return 0

    if args.command == "browser-smoke":
        return asyncio.run(_run_browser_smoke(args))

    if args.command == "demo-vacancy-search":
        return asyncio.run(_run_vacancy_demo(args))

    parser.print_help()
    return 0


def _print_status(config: AppConfig) -> None:
    print("Scout Pilot: фундамент проекта готов.")
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
    print(f"Среда: {config.environment}. Профиль браузера: {config.browser_profile_dir}.")
    print(f"LLM-провайдер: {config.llm_provider}. Модель: {config.llm_model}.")
    mode = "без видимого окна" if config.browser_headless else "с видимым окном"
    print(f"Режим браузера по умолчанию: {mode}.")


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
    demo_settings = VacancySearchDemoSettings(
        start_url=args.start_url,
        query=args.query,
        max_vacancies=args.max_vacancies,
        report_path=report_path,
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
    if result.success:
        return 0
    if result.stop_reason == "confirmation_required":
        return 2
    return 1
