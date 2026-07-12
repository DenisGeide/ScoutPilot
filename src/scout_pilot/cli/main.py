"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from scout_pilot.config import AppConfig
from scout_pilot.runtime.types import DEFAULT_MAX_AGENT_STEPS


_TERMINAL_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_ANSI_BLUE = "\x1b[94m"
_ANSI_RESET = "\x1b[0m"
_OSC_LINK_END = "\x1b]8;;\x1b\\"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout-pilot",
        description="Автономный браузерный агент Scout Pilot.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показать внутренние структурированные логи уровня INFO.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Показать подробные внутренние структурированные логи уровня DEBUG.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Показать готовность проекта и доступные команды.")

    menu_parser = subparsers.add_parser(
        "menu",
        help="Открыть удобное меню запуска без длинных команд.",
    )
    menu_parser.add_argument(
        "--provider",
        choices=("codex", "mock", "openai", "anthropic"),
        default="codex",
        help="Провайдер по умолчанию для live-запуска из меню.",
    )
    menu_parser.add_argument(
        "--dashboard",
        choices=("compact", "verbose", "off"),
        default="off",
        help="Насколько подробно показывать ход выполнения из меню.",
    )
    menu_parser.add_argument(
        "--max-actions",
        "--max-iterations",
        dest="max_iterations",
        type=int,
        default=DEFAULT_MAX_AGENT_STEPS,
        help="Максимум автономных шагов агента на одну задачу.",
    )
    menu_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запускать браузер без видимого окна. Для демо обычно не нужно.",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Проверить локальную среду перед demo: Python, пакет, Playwright, Chromium, .env, Git ignore.",
    )
    doctor_parser.add_argument(
        "--provider",
        choices=("openai", "anthropic"),
        help="Дополнительно проверить наличие локального API-ключа для выбранного live-провайдера.",
    )

    replay_summary_parser = subparsers.add_parser(
        "replay-summary",
        help="Показать безопасную русскую сводку JSON report/replay.",
    )
    replay_summary_parser.add_argument(
        "path",
        help="Путь к JSON report/replay, например reports/tmp/task-replay.json.",
    )

    provider_smoke_parser = subparsers.add_parser(
        "provider-smoke",
        help="Вручную проверить live-интеграцию OpenAI или Anthropic без браузера.",
    )
    provider_smoke_parser.add_argument(
        "--provider",
        required=True,
        choices=("openai", "anthropic", "codex"),
        help="Провайдер для ручной smoke-проверки.",
    )

    profile_info_parser = subparsers.add_parser(
        "profile-info",
        help="Показать путь persistent profile браузера и защиту от коммита.",
    )
    profile_info_parser.add_argument(
        "--profile",
        default="default",
        help="Имя профиля. default использует SCOUT_PILOT_BROWSER_PROFILE_DIR.",
    )

    profile_open_parser = subparsers.add_parser(
        "profile-open",
        help="Открыть браузер с persistent profile для ручного входа на сайт.",
    )
    profile_open_parser.add_argument(
        "--profile",
        default="default",
        help="Имя профиля. default использует SCOUT_PILOT_BROWSER_PROFILE_DIR.",
    )
    profile_open_parser.add_argument(
        "--start-url",
        required=True,
        help="URL, который нужно открыть для ручного входа или проверки сессии.",
    )
    profile_open_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить браузер без видимого окна. Для демо обычно не нужен.",
    )
    profile_open_parser.add_argument(
        "--headed",
        action="store_true",
        help="Принудительно открыть видимое окно браузера. Это режим по умолчанию.",
    )
    profile_open_parser.add_argument(
        "--hold-seconds",
        type=float,
        help="Для smoke-проверки: закрыть браузер автоматически через N секунд.",
    )

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
        help="Запустить live-режим: видимый браузер, runtime, LLM/tool loop и отчеты.",
    )
    run_parser.add_argument(
        "--start-url",
        help="Начальный URL, который агент откроет через Tool Runtime перед циклом.",
    )
    run_parser.add_argument(
        "--provider",
        choices=("openai", "anthropic", "codex", "mock"),
        help="LLM-провайдер для live-режима. По умолчанию берется из .env.",
    )
    run_parser.add_argument(
        "--max-actions",
        "--max-iterations",
        dest="max_iterations",
        type=int,
        default=DEFAULT_MAX_AGENT_STEPS,
        help="Максимум автономных шагов агента на одну задачу.",
    )
    run_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить live-браузер без видимого окна.",
    )
    run_parser.add_argument(
        "--headed",
        action="store_true",
        help="Принудительно запустить видимое окно браузера. Это режим по умолчанию для --live.",
    )
    run_parser.add_argument("--report-path", help="Куда сохранить JSON-отчет.")
    run_parser.add_argument("--replay-path", help="Куда сохранить JSON replay.")
    run_parser.add_argument(
        "--dashboard",
        choices=("compact", "verbose", "off"),
        default="compact",
        help="Показывать компактный/подробный статус выполнения или только короткие сообщения.",
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
        help="Запускать введенные задачи в live-режиме.",
    )
    interactive_parser.add_argument(
        "--provider",
        choices=("openai", "anthropic", "codex", "mock"),
        help="LLM-провайдер для live-задач. По умолчанию берется из .env.",
    )
    interactive_parser.add_argument(
        "--max-actions",
        "--max-iterations",
        dest="max_iterations",
        type=int,
        default=DEFAULT_MAX_AGENT_STEPS,
        help="Максимум автономных шагов агента на одну live-задачу.",
    )
    interactive_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запускать live-браузер без видимого окна.",
    )
    interactive_parser.add_argument(
        "--report-dir",
        help="Папка для отчетов и replay-файлов. По умолчанию reports/tmp.",
    )
    interactive_parser.add_argument(
        "--dashboard",
        choices=("compact", "verbose", "off"),
        default="compact",
        help="Показывать компактный/подробный статус выполнения или только короткие сообщения.",
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
        help="Устаревший совместимый флаг: безопасный ввод в поиск разрешается автоматически.",
    )
    demo_parser.add_argument(
        "--confirm-search-submit",
        action="store_true",
        help="Устаревший совместимый флаг: безопасный запуск поиска разрешается автоматически.",
    )
    demo_parser.add_argument(
        "--probe-security",
        action="store_true",
        help="Проверить, что отклик/сообщение останавливается на подтверждении.",
    )

    interview_parser = subparsers.add_parser(
        "interview-demo",
        help="Запустить локальное демо для интервью на тестовых страницах.",
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
        help="Постоянный профиль браузера для демо. По умолчанию .browser-profiles/interview-demo.",
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
        help="Запустить локальное демо без видимого окна.",
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
        help="Небольшая задержка действий браузера для записи видео.",
    )
    interview_parser.add_argument(
        "--wait-after-search-ms",
        type=int,
        default=200,
        help="Сколько миллисекунд ждать после запуска поиска на тестовой странице.",
    )

    live_local_parser = subparsers.add_parser(
        "live-local-demo",
        help="Запустить локальное live-demo через обычный автономный runtime.",
    )
    live_local_parser.add_argument(
        "task",
        nargs="*",
        help="Текст задачи. Если не указан, используется стандартная задача про AI Engineer вакансии.",
    )
    live_local_parser.add_argument(
        "--provider",
        choices=("mock", "openai", "anthropic", "codex"),
        default="mock",
        help="LLM-провайдер. Для детерминированного demo используйте mock.",
    )
    live_local_parser.add_argument(
        "--dashboard",
        choices=("compact", "verbose", "off"),
        default="compact",
        help="Показывать компактную/подробную live-трассу инструментов.",
    )
    live_local_parser.add_argument(
        "--max-actions",
        "--max-iterations",
        dest="max_iterations",
        type=int,
        default=8,
        help="Максимум автономных шагов агента в локальном demo.",
    )
    live_local_parser.add_argument(
        "--site-dir",
        help="Куда сгенерировать локальный сайт. По умолчанию reports/tmp/live-local-demo-site.",
    )
    live_local_parser.add_argument(
        "--profile-dir",
        help="Постоянный профиль браузера. По умолчанию .browser-profiles/live-local-demo.",
    )
    live_local_parser.add_argument(
        "--report-path",
        help="Куда сохранить JSON-отчет. По умолчанию reports/tmp/live-local-demo-report.json.",
    )
    live_local_parser.add_argument(
        "--replay-path",
        help="Куда сохранить JSON replay. По умолчанию reports/tmp/live-local-demo-replay.json.",
    )
    live_local_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить demo без видимого окна.",
    )
    live_local_parser.add_argument(
        "--headed",
        action="store_true",
        help="Принудительно запустить видимое окно браузера. Это режим по умолчанию.",
    )
    live_local_parser.add_argument(
        "--slow-mo-ms",
        type=int,
        default=80,
        help="Небольшая задержка действий браузера для записи видео.",
    )

    mail_parser = subparsers.add_parser(
        "mail-spam-demo",
        help="Запустить локальное синтетическое демо: прочитать 10 писем, найти спам и остановиться перед удалением.",
    )
    mail_parser.add_argument(
        "--site-dir",
        help="Куда сгенерировать локальный почтовый сайт. По умолчанию reports/tmp/mail-spam-demo-site.",
    )
    mail_parser.add_argument(
        "--profile-dir",
        help="Постоянный профиль браузера для демо. По умолчанию .browser-profiles/mail-spam-demo.",
    )
    mail_parser.add_argument(
        "--report-path",
        help="Куда сохранить JSON-отчет. По умолчанию reports/tmp/mail-spam-demo-report.json.",
    )
    mail_parser.add_argument(
        "--replay-path",
        help="Куда сохранить JSON replay. По умолчанию reports/tmp/mail-spam-demo-replay.json.",
    )
    mail_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить демо без видимого окна браузера.",
    )
    mail_parser.add_argument(
        "--headed",
        action="store_true",
        help="Принудительно запустить видимое окно браузера. Это режим по умолчанию.",
    )
    mail_parser.add_argument(
        "--slow-mo-ms",
        type=int,
        default=80,
        help="Небольшая задержка действий браузера для записи видео.",
    )

    food_parser = subparsers.add_parser(
        "food-order-demo",
        help="Запустить локальное синтетическое демо заказа еды и остановиться перед оплатой.",
    )
    food_parser.add_argument(
        "--site-dir",
        help="Куда сгенерировать локальный сайт заказа еды. По умолчанию reports/tmp/food-order-demo-site.",
    )
    food_parser.add_argument(
        "--profile-dir",
        help="Постоянный профиль браузера для демо. По умолчанию .browser-profiles/food-order-demo.",
    )
    food_parser.add_argument(
        "--report-path",
        help="Куда сохранить JSON-отчет. По умолчанию reports/tmp/food-order-demo-report.json.",
    )
    food_parser.add_argument(
        "--replay-path",
        help="Куда сохранить JSON replay. По умолчанию reports/tmp/food-order-demo-replay.json.",
    )
    food_parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить демо без видимого окна браузера.",
    )
    food_parser.add_argument(
        "--headed",
        action="store_true",
        help="Принудительно запустить видимое окно браузера. Это режим по умолчанию.",
    )
    food_parser.add_argument(
        "--slow-mo-ms",
        type=int,
        default=80,
        help="Небольшая задержка действий браузера для записи видео.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(verbose=args.verbose, debug=args.debug)

    if args.command in (None, "status"):
        config = AppConfig.load()
        _print_status(config)
        return 0

    if args.command == "menu":
        try:
            return asyncio.run(_run_menu(args))
        except KeyboardInterrupt:
            print("Меню закрыто пользователем.")
            return 130

    if args.command == "doctor":
        return asyncio.run(_run_doctor(args))

    if args.command == "replay-summary":
        return _run_replay_summary(args)

    if args.command == "run":
        try:
            return asyncio.run(_run_task(args))
        except KeyboardInterrupt:
            print("Задача отменена пользователем.")
            return 130

    if args.command == "provider-smoke":
        return asyncio.run(_run_provider_smoke(args))

    if args.command == "profile-info":
        return _run_profile_info(args)

    if args.command == "profile-open":
        return asyncio.run(_run_profile_open(args))

    if args.command == "interactive":
        try:
            return asyncio.run(_run_interactive(args))
        except KeyboardInterrupt:
            print("Интерактивный режим отменен пользователем.")
            return 130

    if args.command == "browser-smoke":
        return asyncio.run(_run_browser_smoke(args))

    if args.command == "demo-vacancy-search":
        return asyncio.run(_run_vacancy_demo(args))

    if args.command == "interview-demo":
        return asyncio.run(_run_interview_demo(args))

    if args.command == "live-local-demo":
        return asyncio.run(_run_live_local_demo(args))

    if args.command == "mail-spam-demo":
        return asyncio.run(_run_mail_spam_demo(args))

    if args.command == "food-order-demo":
        return asyncio.run(_run_food_order_demo(args))

    parser.print_help()
    return 0


def _print_status(config: AppConfig) -> None:
    print("Scout Pilot: проект установлен, основные слои доступны.")
    print(
        "Подключены Browser Engine, Semantic Observation Engine, Tool Runtime, "
        "LLM Provider Layer, Planning Engine, Memory, Runtime, Context Budgeting, "
        "Security Policy, Universal Semantic Navigation и слой demo/reporting."
    )
    print(
        "Live-режим запускается через scout-pilot run \"текст задачи\" --live "
        "--start-url <URL>. Для детерминированной проверки используйте --provider mock."
    )
    print("Удобный режим без длинных команд доступен через scout-pilot menu.")
    print("Локальное scripted demo доступно через scout-pilot interview-demo.")
    print("Основное runtime demo доступно через scout-pilot live-local-demo.")
    print("Синтетическое почтовое demo доступно через scout-pilot mail-spam-demo.")
    print("Синтетическое demo заказа еды доступно через scout-pilot food-order-demo.")
    print("Сводка report/replay доступна через scout-pilot replay-summary <path>.")
    print("Проверка локальной среды доступна через scout-pilot doctor.")
    print("Persistent profile можно проверить через scout-pilot profile-info и profile-open.")
    print("Безопасный сухой запуск: scout-pilot run \"текст задачи\" --dry-run.")
    print("Ручная проверка LLM: scout-pilot provider-smoke --provider openai|anthropic.")
    print("Интерактивный режим доступен через scout-pilot interactive.")
    print(f"Среда: {config.environment}. Профиль браузера: {config.browser_profile_dir}.")
    print(f"LLM-провайдер: {config.llm_provider}. Модель: {config.llm_model}.")
    mode = "без видимого окна" if config.browser_headless else "с видимым окном"
    print(f"Режим браузера по умолчанию: {mode}.")


_MENU_DEFAULT_TASK = (
    "Найди три подходящие AI Engineer вакансии, прочитай описания, "
    "сравни требования и остановись перед откликом."
)


def _menu_lines() -> tuple[str, ...]:
    return (
        "",
        "Scout Pilot - меню запуска",
        "0 - Чат с агентом: сайт открыт, задачи пишутся обычным текстом",
        "1 - Локальное demo вакансий через реальный runtime",
        "2 - Открыть persistent profile для ручного входа",
        "3 - Проверить live-провайдера Codex/OpenAI/Anthropic",
        "4 - Показать краткую сводку последнего report/replay",
        "5 - Проверить окружение командой doctor",
        "6 - Быстро проверить запуск браузера",
        "7 - Локальное demo: почта и остановка перед spam/delete",
        "8 - Локальное demo: заказ еды и остановка перед оплатой",
        "9 - Выйти",
    )


async def _run_menu(args: argparse.Namespace) -> int:
    print("Открываю меню Scout Pilot. Для демо браузер запускается видимым по умолчанию.")
    while True:
        for line in _menu_lines():
            print(line)
        choice = _menu_read("Выберите пункт: ").strip().casefold()
        if choice in {"", "9", "q", "quit", "exit", "выход"}:
            print("Меню закрыто.")
            return 0
        if choice == "0":
            await _menu_run_agent(args)
            continue
        if choice == "1":
            await _menu_run_live_local_demo(args)
            continue
        if choice == "2":
            await _menu_open_profile(args)
            continue
        if choice == "3":
            await _menu_provider_smoke()
            continue
        if choice == "4":
            _menu_show_latest_replay()
            continue
        if choice == "5":
            await _run_doctor(argparse.Namespace(provider=None))
            continue
        if choice == "6":
            await _run_browser_smoke(
                argparse.Namespace(
                    url=None,
                    headless=bool(args.headless),
                    headed=not bool(args.headless),
                    hold_seconds=3.0,
                )
            )
            continue
        if choice == "7":
            await _run_mail_spam_demo(_menu_demo_namespace("mail-spam", args))
            continue
        if choice == "8":
            await _run_food_order_demo(_menu_demo_namespace("food-order", args))
            continue
        print("Не понял пункт меню. Введите число от 0 до 9.")


async def _menu_run_agent(args: argparse.Namespace) -> int:
    print("")
    print("Чат с агентом: браузер остается открытым, пока вы не выйдете из режима.")
    print("Сначала откроем сайт. Потом пишите задачи обычным текстом.")
    print("Команды: /url - сменить сайт, /report - последний отчет, /debug - подробный режим, /exit - выйти.")
    start_url = _menu_prompt_start_url()
    provider = _menu_fast_provider(args, start_url)
    print(f"Провайдер: {provider}. Лимит шагов агента на задачу: {args.max_iterations}.")
    return await _menu_chat_session(
        args,
        start_url=start_url,
        provider=provider,
    )


def _menu_task_help() -> None:
    print("Пример: есть ли на странице поле поиска?")
    print("Пример: найди AI Engineer вакансии с зарплатой выше 1500 долларов.")
    print("Пример: открой отклики и приглашения и кратко скажи, что там есть.")
    print("Команды: /url, /report, /debug, /exit.")


def _menu_prompt_start_url() -> str:
    while True:
        raw_url = _menu_read(
            "URL сайта (Enter - https://hh.ru): "
        ).strip()
        if not raw_url:
            raw_url = "https://hh.ru"
        start_url, error = _normalize_menu_start_url(raw_url)
        if error is None:
            return start_url or "https://hh.ru"
        print(error)


def _normalize_menu_start_url(raw_url: str) -> tuple[str | None, str | None]:
    value = raw_url.strip()
    if not value:
        return None, None
    lowered = value.casefold()
    if lowered in {"9", "exit", "quit", "/exit", "выход"}:
        return None, None
    if " " in value or lowered.rstrip(":") in {"url", "стартовый url", "сайт"}:
        return None, "Введите настоящий URL, например https://hh.ru, или нажмите Enter для demo."
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "URL должен начинаться с http:// или https://, например https://hh.ru."
    return value, None


def _menu_fast_provider(args: argparse.Namespace, start_url: str | None) -> str:
    if args.provider != "mock":
        return args.provider
    config = AppConfig.load()
    if start_url and config.provider_secrets.has_openai_key:
        print("В .env найден OPENAI_API_KEY, для живого сайта автоматически выбран OpenAI.")
        return "openai"
    if start_url:
        print(
            "Сейчас выбран provider mock. Для настоящего сайта он подходит только для безопасной "
            "проверки запуска; для реального рассуждения добавьте OPENAI_API_KEY или запустите "
            "scout-pilot menu --provider openai."
        )
    return "mock"


async def _menu_chat_session(
    args: argparse.Namespace,
    *,
    start_url: str,
    provider: str,
) -> int:
    from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
    from scout_pilot.cli.task_session import (
        _create_provider,
        _provider_start_error_ru,
    )
    from scout_pilot.memory import HierarchicalMemory
    from scout_pilot.models import ToolRequest
    from scout_pilot.observation import ObservationSettings, SemanticObservationEngine
    from scout_pilot.tools import (
        DefaultToolRuntime,
        ToolContext,
        create_browser_tool_registry,
    )

    config = AppConfig.load()
    try:
        llm_provider = _create_provider(provider, config)
    except Exception as exc:
        print(_provider_start_error_ru(provider, exc))
        return 1

    browser_settings = replace(
        BrowserEngineConfig.from_app_config(config),
        headless=bool(args.headless),
        slow_mo_ms=120,
    )
    browser = PlaywrightBrowserEngine(browser_settings)
    observation_engine = SemanticObservationEngine(
        browser,
        ObservationSettings.from_app_config(config),
    )
    registry = create_browser_tool_registry()
    tool_runtime = DefaultToolRuntime(
        registry,
        ToolContext(browser=browser, observation_engine=observation_engine),
    )
    tool_schemas = registry.schemas()
    memory = HierarchicalMemory()
    debug_output = args.dashboard == "verbose"
    last_report: Path | None = None
    last_replay: Path | None = None

    try:
        print("Открываю браузер...")
        await browser.start()
        navigation = await tool_runtime.execute(ToolRequest("browser.navigate", {"url": start_url}))
        if not navigation.success:
            print(f"Не удалось открыть сайт: {navigation.message}")
            return 1
        state = await browser.current_state()
        title = state.title or "страница открыта"
        print(f"Открыл: {title}")
        print(f"URL: {state.url or start_url}")
        print("Пишите, что нужно сделать. Браузер останется открытым до выхода.")
        _menu_task_help()

        while True:
            task_text = _menu_read("\nВы > ").strip()
            normalized = task_text.casefold()
            if normalized in {"", "help", "/help", "?"}:
                _menu_task_help()
                continue
            if normalized in {"9", "exit", "quit", "/exit", "выход"}:
                print("Останавливаю режим. Сейчас закрою браузер и сохраненные сессии останутся в profile.")
                return 0
            if normalized == "/debug":
                debug_output = not debug_output
                mode = "подробный" if debug_output else "краткий"
                print(f"Режим вывода: {mode}.")
                continue
            if normalized == "/report":
                if last_replay is None and last_report is None:
                    print("Отчета пока нет: сначала выполните задачу.")
                else:
                    _run_replay_summary(
                        argparse.Namespace(path=str(last_replay or last_report))
                    )
                continue
            if normalized == "/url":
                start_url = _menu_prompt_start_url()
                navigation = await tool_runtime.execute(
                    ToolRequest("browser.navigate", {"url": start_url})
                )
                if navigation.success:
                    state = await browser.current_state()
                    print(f"Открыл: {state.title or start_url}")
                else:
                    print(f"Не удалось открыть сайт: {navigation.message}")
                continue

            result = await _menu_chat_run_task(
                task_text=task_text,
                provider=llm_provider,
                config=config,
                observation_engine=observation_engine,
                planning_provider=llm_provider,
                tool_runtime=tool_runtime,
                memory=memory,
                tool_schemas=tool_schemas,
                max_iterations=args.max_iterations,
                debug_output=debug_output,
            )
            last_report = result["report_path"]
            last_replay = result["replay_path"]
            if result["success"]:
                print("Можно написать следующую задачу.")
            else:
                print("Задача остановилась. Можно уточнить запрос и попробовать еще раз.")
    finally:
        await browser.stop()
        print("Браузер закрыт.")


async def _menu_chat_run_task(
    *,
    task_text: str,
    provider: object,
    planning_provider: object,
    config: AppConfig,
    observation_engine: object,
    tool_runtime: object,
    memory: object,
    tool_schemas: tuple[object, ...],
    max_iterations: int,
    debug_output: bool,
) -> dict[str, object]:
    from scout_pilot.cli.dashboard import RuntimeDashboard
    from scout_pilot.cli.task_session import (
        CliTaskSettings,
        _ask_user_confirmation,
        _can_resume_after_confirmation,
        _event_with_trace,
        _result_message_ru,
        default_artifact_paths,
    )
    from scout_pilot.llm import ReasoningEngine, ReasoningSettings
    from scout_pilot.models import UserTask
    from scout_pilot.planning import ProviderPlanningEngine
    from scout_pilot.planning.types import PlanningSettings
    from scout_pilot.reporting import RuntimeReportRecorder
    from scout_pilot.runtime import AutonomousAgentRuntime, RuntimeSettings
    from scout_pilot.runtime.types import TaskTerminationReason

    paths = default_artifact_paths(config.reports_dir / "tmp", prefix="chat")
    settings = CliTaskSettings(
        task=task_text,
        dry_run=False,
        report_path=paths.report_path,
        replay_path=paths.replay_path,
        dashboard="off",
        max_iterations=max_iterations,
    )
    recorder = RuntimeReportRecorder(
        task=task_text,
        mode="cli_chat",
        dry_run=False,
    )
    dashboard = RuntimeDashboard(task=task_text)
    runtime = AutonomousAgentRuntime(
        observation_engine=observation_engine,
        reasoning_engine=ReasoningEngine(
            provider,
            ReasoningSettings(
                model=config.llm_model,
                max_output_tokens=config.llm_max_output_tokens,
                timeout_seconds=config.llm_timeout_seconds,
                max_input_tokens=config.max_context_tokens,
            ),
        ),
        planning_engine=ProviderPlanningEngine(
            planning_provider,
            PlanningSettings(
                max_input_tokens=config.max_context_tokens,
                max_output_tokens=config.llm_max_output_tokens,
                timeout_seconds=config.llm_timeout_seconds,
            ),
        ),
        tool_runtime=tool_runtime,
        memory=memory,
        tool_schemas=tool_schemas,
        settings=RuntimeSettings(max_iterations=max_iterations),
        security_constraints=(
            "Перед отправкой форм, откликами, сообщениями, покупками, загрузкой файлов "
            "или удалением данных нужно явное подтверждение пользователя."
        ),
        confirmation_constraints=(
            "Никогда не продолжай автоматически после confirmation_required.",
        ),
        budget={"remaining_tokens": config.max_context_tokens},
    )

    print("Агент: понял задачу, смотрю текущую страницу.")
    success = False
    final_message = "Задача остановлена до результата."
    try:
        while True:
            async for event in runtime.run(UserTask(task_text)):
                _menu_record_chat_event(
                    settings,
                    dashboard,
                    recorder,
                    event,
                    debug_output=debug_output,
                )

            if not _can_resume_after_confirmation(runtime):
                break

            confirmation = runtime.pending_confirmation
            if confirmation is None:
                break
            if _ask_user_confirmation(confirmation, print):
                confirmation_id = str(confirmation.get("confirmation_id") or "")
                if runtime.confirm_pending_action(confirmation_id):
                    print("Агент: подтверждение принято, выполняю только это действие.")
                    continue
            confirmation_id = str(confirmation.get("confirmation_id") or "")
            if confirmation_id:
                runtime.reject_pending_action(confirmation_id)
            final_message = "Действие отменено. Я остановился без внешнего эффекта."
            recorder.finalize(success=False, summary_ru=final_message, failure_ru=final_message)
            artifacts = recorder.write(
                report_path=paths.report_path,
                replay_path=paths.replay_path,
            )
            print(f"Агент: {final_message}")
            print(f"Отчет: {artifacts.report_path}")
            return {
                "success": False,
                "report_path": artifacts.report_path,
                "replay_path": artifacts.replay_path,
            }

        if runtime.last_result is None:
            final_message = "Не удалось получить итоговый ответ."
            success = False
        else:
            success = runtime.last_result.success
            final_message = _result_message_ru(runtime.last_result)
            if runtime.last_result.termination_reason is TaskTerminationReason.WAITING_FOR_CONFIRMATION:
                success = False
        recorder.finalize(
            success=success,
            summary_ru=final_message,
            failure_ru=None if success else final_message,
        )
        artifacts = recorder.write(
            report_path=paths.report_path,
            replay_path=paths.replay_path,
        )
        print("")
        _print_agent_final_message(final_message)
        print(f"Отчет: {artifacts.report_path}")
        return {
            "success": success,
            "report_path": artifacts.report_path,
            "replay_path": artifacts.replay_path,
        }
    except Exception as exc:
        final_message = (
            f"Задача остановилась из-за ошибки {type(exc).__name__}. "
            "Браузер остается в рамках текущей сессии, отчет сохранен."
        )
        recorder.finalize(success=False, summary_ru=final_message, failure_ru=final_message)
        artifacts = recorder.write(
            report_path=paths.report_path,
            replay_path=paths.replay_path,
        )
        print(f"Агент: {final_message}")
        print(f"Отчет: {artifacts.report_path}")
        return {
            "success": False,
            "report_path": artifacts.report_path,
            "replay_path": artifacts.replay_path,
        }


def _print_agent_final_message(message: str) -> None:
    print(f"Агент: {_format_terminal_links(message)}")


def _format_terminal_links(message: str, *, use_color: bool | None = None) -> str:
    """Keep exact links in artifacts while presenting concise terminal links."""
    color_enabled = sys.stdout.isatty() if use_color is None else use_color

    def replace_url(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        trailing = ""
        while raw_url and raw_url[-1] in ".,;:!?)]}":
            trailing = raw_url[-1] + trailing
            raw_url = raw_url[:-1]

        parsed = urlparse(raw_url)
        display_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
        if len(display_url) > 64:
            path_tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
            display_url = f"{parsed.scheme}://{parsed.netloc}/.../{path_tail}"

        rendered = display_url
        if color_enabled:
            link_start = f"\x1b]8;;{raw_url}\x1b\\"
            rendered = (
                f"{link_start}{_ANSI_BLUE}{display_url}{_ANSI_RESET}{_OSC_LINK_END}"
            )
        return f"{rendered}  [Ctrl + клик мыши - открыть]{trailing}"

    return _TERMINAL_URL_RE.sub(replace_url, message)


def _menu_record_chat_event(
    settings: object,
    dashboard: object,
    recorder: object,
    event: object,
    *,
    debug_output: bool,
) -> None:
    from scout_pilot.cli.task_session import _event_with_trace

    dashboard.update(event)
    recorder.record_event(_event_with_trace(event, dashboard.trace()))
    message = _menu_chat_event_message(event, debug_output=debug_output)
    if message:
        print(f"Агент: {message}")


def _menu_chat_event_message(event: object, *, debug_output: bool) -> str:
    name = str(getattr(event, "name", ""))
    details = dict(getattr(event, "details", {}))
    if name == "observation_captured":
        if not debug_output:
            return ""
        title = str(details.get("title") or "").strip()
        return f"вижу страницу: {title}." if title else "смотрю страницу."
    if name == "page_blocker_detected":
        if details.get("stop") is True:
            return "на странице есть блокер; я не буду обходить CAPTCHA или автоматизировать вход."
        return "страница еще загружается или пока пуста; проверяю доступный контекст."
    if name == "modal_dismiss_started":
        return "закрываю всплывающее окно, не относящееся к задаче..."
    if name == "modal_dismiss_finished":
        if details.get("dismissed") is True:
            return "всплывающее окно закрыто; продолжаю задачу."
        return "не удалось безопасно закрыть окно; учитываю его как блокер."
    if name == "tool_selected":
        tool = str(details.get("selected_tool") or details.get("tool_name") or "")
        action = _menu_tool_action_ru(tool)
        if debug_output:
            arguments = details.get("selected_tool_arguments") or {}
            return f"{action} ({tool}, аргументы: {json.dumps(arguments, ensure_ascii=False)})"
        return action
    if name == "tool_execution_finished":
        if debug_output:
            status = details.get("tool_status")
            message = details.get("message")
            return f"результат инструмента: {status}; {message}"
        if details.get("success") is True:
            tool = str(details.get("tool_name") or "")
            return _menu_tool_done_ru(tool)
        if details.get("tool_status") == "paused":
            return "нужно подтверждение перед внешним действием."
        message = str(details.get("message") or "").strip()
        return f"действие не удалось: {message}" if message else "действие не удалось."
    if name == "repeated_target_blocked":
        return "эту страницу уже открывал; выбираю другую вакансию."
    if name == "repeated_target_remapped":
        return "эта ссылка уже прочитана; автоматически выбираю другой похожий результат."
    if name == "confirmation_required":
        return "останавливаюсь перед действием, которое требует подтверждения."
    return ""


def _menu_tool_action_ru(tool_name: str) -> str:
    return {
        "browser.navigate": "открываю страницу...",
        "browser.back": "возвращаюсь к предыдущей странице...",
        "browser.observe": "проверяю, что видно на странице...",
        "browser.resolve_target": "ищу подходящий элемент на странице...",
        "browser.click": "нажимаю нужный элемент...",
        "browser.click_by_intent": "нажимаю подходящую кнопку или ссылку...",
        "browser.fill": "ввожу текст в поле...",
        "browser.fill_by_label": "заполняю поле по смысловой подписи...",
        "browser.plan_form_fill": "сопоставляю поля формы с задачей...",
        "browser.press_key": "нажимаю клавишу...",
        "browser.wait": "жду завершения загрузки данных...",
        "browser.screenshot": "делаю диагностический снимок...",
    }.get(tool_name, "выполняю действие...")


def _menu_tool_done_ru(tool_name: str) -> str:
    if tool_name == "browser.observe":
        return ""
    return {
        "browser.navigate": "страница открыта.",
        "browser.back": "вернулся к предыдущей странице.",
        "browser.resolve_target": "нашел подходящий элемент.",
        "browser.click": "клик выполнен.",
        "browser.click_by_intent": "клик выполнен.",
        "browser.fill": "текст введен.",
        "browser.fill_by_label": "текст введен.",
        "browser.plan_form_fill": "поля формы сопоставлены.",
        "browser.press_key": "клавиша нажата.",
        "browser.wait": "ожидание завершено.",
        "browser.screenshot": "снимок сохранен.",
    }.get(tool_name, "готово.")


async def _menu_run_live_local_demo(args: argparse.Namespace) -> int:
    task = _menu_read(
        "Задача demo (Enter - стандартная задача про AI Engineer вакансии): "
    ).strip()
    if not task:
        task = _MENU_DEFAULT_TASK
    provider = _menu_choice(
        "Провайдер",
        ("mock", "openai", "anthropic"),
        default=args.provider,
    )
    print("Запускаю видимый браузер и локальный сайт. Внешние заявки не отправляются.")
    return await _run_live_local_demo(
        argparse.Namespace(
            task=[task],
            provider=provider,
            dashboard=args.dashboard,
            max_iterations=args.max_iterations,
            site_dir=None,
            profile_dir=None,
            report_path=None,
            replay_path=None,
            headless=bool(args.headless),
            headed=not bool(args.headless),
            slow_mo_ms=120,
        )
    )


async def _menu_open_profile(args: argparse.Namespace) -> int:
    profile = _menu_read("Имя профиля (Enter - default): ").strip() or "default"
    start_url = _menu_read("URL для ручного входа (Enter - https://hh.ru): ").strip()
    if not start_url:
        start_url = "https://hh.ru"
    print("Открою persistent profile. Войдите вручную, затем закройте браузер или нажмите Enter.")
    return await _run_profile_open(
        argparse.Namespace(
            profile=profile,
            start_url=start_url,
            headless=bool(args.headless),
            headed=not bool(args.headless),
            hold_seconds=None,
        )
    )


async def _menu_provider_smoke() -> int:
    provider = _menu_choice(
        "Провайдер для smoke-проверки",
        ("codex", "openai", "anthropic"),
        default="codex",
    )
    return await _run_provider_smoke(argparse.Namespace(provider=provider))


def _menu_show_latest_replay() -> int:
    path_text = _menu_read(
        "Путь к report/replay (Enter - последний replay из reports/tmp): "
    ).strip()
    path = Path(path_text) if path_text else _latest_replay_path()
    if path is None:
        print("Replay пока не найден. Сначала запустите demo или live-задачу.")
        return 1
    print(f"Показываю безопасную сводку: {path}")
    return _run_replay_summary(argparse.Namespace(path=str(path)))


def _latest_replay_path() -> Path | None:
    config = AppConfig.load()
    report_dir = config.reports_dir / "tmp"
    if not report_dir.exists():
        return None
    candidates = [
        path
        for path in report_dir.rglob("*.json")
        if "replay" in path.name.casefold()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _menu_demo_namespace(kind: str, args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        site_dir=None,
        profile_dir=None,
        report_path=None,
        replay_path=None,
        headless=bool(args.headless),
        headed=not bool(args.headless),
        slow_mo_ms=120,
        kind=kind,
    )


def _menu_choice(label: str, choices: tuple[str, ...], *, default: str) -> str:
    choices_text = "/".join(choices)
    while True:
        value = (
            _menu_read(f"{label} [{choices_text}] (Enter - {default}): ")
            .strip()
            .casefold()
        )
        if not value:
            return default
        if value in choices:
            return value
        print(f"Введите одно из значений: {choices_text}.")


def _menu_int(
    label: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    while True:
        value = _menu_read(f"{label} (Enter - {default}): ").strip()
        if not value:
            return default
        try:
            number = int(value)
        except ValueError:
            print("Введите число.")
            continue
        if minimum <= number <= maximum:
            return number
        print(f"Введите число от {minimum} до {maximum}.")


def _menu_read(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        print("Меню требует интерактивного ввода. Завершаю без запуска действий.")
        return "9"


async def _run_doctor(args: argparse.Namespace) -> int:
    from scout_pilot.cli.doctor import (
        DoctorSettings,
        format_doctor_report,
        run_doctor,
    )

    report = await run_doctor(DoctorSettings(provider=args.provider))
    for line in format_doctor_report(report):
        print(line)
    return report.exit_code


def _run_replay_summary(args: argparse.Namespace) -> int:
    from scout_pilot.reporting import ReplaySummaryError, summarize_replay_file

    try:
        summary = summarize_replay_file(Path(args.path))
    except ReplaySummaryError as exc:
        print(str(exc))
        return 2

    for line in summary.lines:
        print(line)
    return 0 if summary.safe_to_print else 2


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
        start_url=args.start_url,
        provider=args.provider,
        max_iterations=args.max_iterations,
        headless=False if args.headed else bool(args.headless),
    )
    result = await run_cli_task(settings, progress=print)
    if result.success:
        return 0
    print(
        "Что можно сделать дальше: проверьте отчет/replay, уточните задачу, "
        "стартовый URL или настройки LLM-провайдера. Для безопасной проверки "
        "можно повторить команду с --dry-run или --provider mock."
    )
    return 1


async def _run_provider_smoke(args: argparse.Namespace) -> int:
    from scout_pilot.cli.provider_smoke import (
        ProviderSmokeSettings,
        run_provider_smoke,
    )

    result = await run_provider_smoke(ProviderSmokeSettings(provider=args.provider))
    print(result.message_ru)
    return result.exit_code


def _run_profile_info(args: argparse.Namespace) -> int:
    from scout_pilot.cli.profiles import inspect_browser_profile

    config = AppConfig.load()
    try:
        profile = inspect_browser_profile(config, profile=args.profile)
    except ValueError as exc:
        print(f"Не удалось проверить профиль: {exc}")
        return 1

    print(f"Профиль: {profile.name}")
    print(f"Путь профиля: {profile.path.resolve()}")
    print(f"Папка существует: {_yes_no_ru(profile.exists)}")
    print(f"Игнорируется Git: {_yes_no_unknown_ru(profile.git_ignored)}")
    print(
        "Не коммитьте browser profile: внутри могут быть cookies, токены, "
        "локальная история и состояние авторизации."
    )
    if profile.git_ignored is False:
        print("Внимание: этот путь не закрыт .gitignore. Перед использованием добавьте его в ignore.")
        return 1
    return 0


async def _run_profile_open(args: argparse.Namespace) -> int:
    from scout_pilot.browser import (
        BrowserEngineConfig,
        BrowserEngineError,
        PlaywrightBrowserEngine,
    )
    from scout_pilot.cli.profiles import inspect_browser_profile

    config = AppConfig.load()
    try:
        profile = inspect_browser_profile(config, profile=args.profile)
    except ValueError as exc:
        print(f"Не удалось открыть профиль: {exc}")
        return 1

    if profile.git_ignored is False:
        print("Профиль не открыт: путь не закрыт .gitignore.")
        print(f"Путь профиля: {profile.path.resolve()}")
        return 1
    if profile.git_ignored is None:
        print("Не удалось проверить .gitignore для профиля. Продолжаю осторожно.")

    browser_settings = replace(
        BrowserEngineConfig.from_app_config(config),
        user_data_dir=profile.path,
        headless=True if args.headless else False,
    )
    if args.headed:
        browser_settings = replace(browser_settings, headless=False)

    engine = PlaywrightBrowserEngine(browser_settings)
    print(f"Открываю профиль: {profile.name}")
    print(f"Путь профиля: {profile.path.resolve()}")
    print("Этот профиль локальный. Не коммитьте его и не экспортируйте storage state в репозиторий.")
    try:
        await engine.start()
        result = await engine.navigate_to(args.start_url)
        if not result.success:
            print(f"Не удалось открыть страницу: {_browser_action_message_ru(result.error_code)}")
            return 1
        print(f"Страница открыта: {result.title or result.url or args.start_url}")
        if args.hold_seconds is not None:
            await asyncio.sleep(max(args.hold_seconds, 0))
            return 0
        print("Войдите на сайт вручную. Затем закройте окно браузера или нажмите Enter в терминале.")
        await _wait_for_enter_or_browser_close(engine)
        return 0
    except BrowserEngineError as exc:
        print(f"Не удалось запустить браузер с persistent profile: {exc}")
        return 1
    finally:
        await engine.stop()
        print("Браузер закрыт. Состояние профиля сохранено локально.")


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
            provider=args.provider,
            max_iterations=args.max_iterations,
            headless=True if args.headless else False,
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
                print(f"Не удалось открыть страницу: {_browser_action_message_ru(result.error_code)}")
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
        print(f"Replay-файл сохранен: {result.replay_path}")
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
    print(f"Replay-файл сохранен: {result.replay_path}")
    print(f"Прочитано страниц: {result.notes_count}. Пауз безопасности: {result.security_pause_count}.")
    if result.success:
        print("Локальное демо для интервью завершено. Реальные отклики и сообщения не отправлялись.")
        return 0
    print("Демо остановилось. Проверьте отчет и replay, чтобы увидеть причину.")
    return 1


async def _run_live_local_demo(args: argparse.Namespace) -> int:
    from scout_pilot.demo import (
        DEFAULT_LIVE_LOCAL_TASK,
        LiveLocalDemoSettings,
        run_live_local_demo,
    )

    config = AppConfig.load()
    task_text = " ".join(args.task).strip() or DEFAULT_LIVE_LOCAL_TASK
    settings = LiveLocalDemoSettings(
        site_dir=(
            Path(args.site_dir)
            if args.site_dir
            else Path("reports/tmp/live-local-demo-site")
        ),
        profile_dir=(
            Path(args.profile_dir)
            if args.profile_dir
            else Path(".browser-profiles/live-local-demo")
        ),
        report_path=(
            Path(args.report_path)
            if args.report_path
            else Path("reports/tmp/live-local-demo-report.json")
        ),
        replay_path=(
            Path(args.replay_path)
            if args.replay_path
            else Path("reports/tmp/live-local-demo-replay.json")
        ),
        task=task_text,
        provider=args.provider,
        dashboard=args.dashboard,
        max_iterations=args.max_iterations,
        headless=True if args.headless else False,
        slow_mo_ms=args.slow_mo_ms,
    )
    if args.headed:
        settings = replace(settings, headless=False)

    result = await run_live_local_demo(config, settings, progress=print)
    print(result.message_ru)
    print(f"Локальный сайт: {result.local_site_url}")
    print(f"Отчет сохранен: {result.report_path}")
    print(f"Replay-файл сохранен: {result.replay_path}")
    print(
        "Прочитано страниц: "
        f"{result.detail_pages_read}. Проверок неоднозначности: {result.ambiguity_checks}. "
        f"Пауз безопасности: {result.security_pause_count}."
    )
    if result.success:
        print("Live local demo завершено как ожидаемая безопасная остановка перед внешним действием.")
        return 0
    print("Live local demo не дошло до ожидаемой остановки. Проверьте report/replay.")
    return 1


async def _run_mail_spam_demo(args: argparse.Namespace) -> int:
    from scout_pilot.demo import MailSpamDemoSettings, run_local_mail_spam_demo

    config = AppConfig.load()
    settings = MailSpamDemoSettings(
        site_dir=(
            Path(args.site_dir)
            if args.site_dir
            else Path("reports/tmp/mail-spam-demo-site")
        ),
        profile_dir=(
            Path(args.profile_dir)
            if args.profile_dir
            else Path(".browser-profiles/mail-spam-demo")
        ),
        report_path=(
            Path(args.report_path)
            if args.report_path
            else Path("reports/tmp/mail-spam-demo-report.json")
        ),
        replay_path=(
            Path(args.replay_path)
            if args.replay_path
            else Path("reports/tmp/mail-spam-demo-replay.json")
        ),
        headless=True if args.headless else False,
        slow_mo_ms=args.slow_mo_ms,
    )
    if args.headed:
        settings = replace(settings, headless=False)

    result = await run_local_mail_spam_demo(config, settings, progress=print)
    print(result.message_ru)
    print(f"Локальный почтовый сайт: {result.local_site_url}")
    print(f"Отчет сохранен: {result.report_path}")
    print(f"Replay-файл сохранен: {result.replay_path}")
    print(
        f"Прочитано писем: {result.messages_read}. "
        f"Вероятный спам: {result.spam_candidates}. "
        f"Пауз безопасности: {result.security_pause_count}."
    )
    if result.success:
        print("Почтовое demo завершено безопасно: реальные аккаунты не использовались, письма не удалялись.")
        return 0
    print("Почтовое demo остановилось раньше ожидаемого. Проверьте report/replay.")
    return 1


async def _run_food_order_demo(args: argparse.Namespace) -> int:
    from scout_pilot.demo import FoodOrderDemoSettings, run_local_food_order_demo

    config = AppConfig.load()
    settings = FoodOrderDemoSettings(
        site_dir=(
            Path(args.site_dir)
            if args.site_dir
            else Path("reports/tmp/food-order-demo-site")
        ),
        profile_dir=(
            Path(args.profile_dir)
            if args.profile_dir
            else Path(".browser-profiles/food-order-demo")
        ),
        report_path=(
            Path(args.report_path)
            if args.report_path
            else Path("reports/tmp/food-order-demo-report.json")
        ),
        replay_path=(
            Path(args.replay_path)
            if args.replay_path
            else Path("reports/tmp/food-order-demo-replay.json")
        ),
        headless=True if args.headless else False,
        slow_mo_ms=args.slow_mo_ms,
    )
    if args.headed:
        settings = replace(settings, headless=False)

    result = await run_local_food_order_demo(config, settings, progress=print)
    print(result.message_ru)
    print(f"Локальный сайт заказа еды: {result.local_site_url}")
    print(f"Отчет сохранен: {result.report_path}")
    print(f"Replay-файл сохранен: {result.replay_path}")
    print(
        f"Позиции в корзине: {', '.join(result.selected_items) or 'нет'}. "
        f"Checkout открыт: {_yes_no_ru(result.checkout_reached)}. "
        f"Пауз безопасности: {result.security_pause_count}."
    )
    if result.success:
        print("Food-order demo завершено безопасно: реальные сервисы и платежи не использовались.")
        return 0
    print("Food-order demo остановилось раньше ожидаемого. Проверьте report/replay.")
    return 1


async def _wait_for_enter_or_browser_close(engine: object) -> None:
    while True:
        state = await engine.current_state()
        if not state.is_started:
            print("Окно браузера закрыто.")
            return
        if _enter_pressed():
            print("Получен Enter, закрываю браузер.")
            return
        await asyncio.sleep(0.25)


def _enter_pressed() -> bool:
    if not sys.stdin.isatty():
        return False
    if sys.platform.startswith("win"):
        import msvcrt

        while msvcrt.kbhit():
            char = msvcrt.getwch()
            if char in {"\r", "\n"}:
                return True
        return False

    import select

    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if not readable:
        return False
    sys.stdin.readline()
    return True


def _yes_no_ru(value: bool) -> str:
    return "да" if value else "нет"


def _yes_no_unknown_ru(value: bool | None) -> str:
    if value is None:
        return "не удалось проверить"
    return _yes_no_ru(value)


def _browser_action_message_ru(error_code: str | None) -> str:
    if error_code and error_code.startswith("http_status_"):
        status = error_code.removeprefix("http_status_")
        return f"сервер ответил ошибкой HTTP {status}."
    return {
        "invalid_url": "URL пустой или использует неподдерживаемую схему.",
        "browser_not_started": "браузер не был запущен.",
        "navigation_timeout": "страница не загрузилась за отведенное время.",
        "navigation_error": "браузер не смог завершить переход.",
    }.get(error_code or "", "подробности записаны во внутренний результат браузерного слоя.")


def _configure_logging(*, verbose: bool, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_StructuredLogFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


def _configure_console_streams() -> None:
    """Prevent uncommon page/model characters from crashing Windows terminals."""

    if not sys.platform.startswith("win"):
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                continue


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
