"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from collections.abc import Sequence

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

    parser.print_help()
    return 0


def _print_status(config: AppConfig) -> None:
    print("Scout Pilot: фундамент проекта готов.")
    print("Browser Engine, Semantic Observation Engine и Tool Runtime подключены.")
    print("LLM-вызовы пока не включены.")
    print(f"Среда: {config.environment}. Профиль браузера: {config.browser_profile_dir}.")
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
