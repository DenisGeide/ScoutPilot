"""Local setup diagnostics for the Scout Pilot CLI."""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import importlib.util
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from scout_pilot.cli.profiles import inspect_browser_profile, is_ignored_by_git
from scout_pilot.config import AppConfig


CheckStatus = str
CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DoctorSettings:
    """Settings for local setup diagnostics."""

    provider: str | None = None
    env_file: Path | None = Path(".env")
    cwd: Path | None = None

    def __post_init__(self) -> None:
        if self.provider is not None and self.provider not in {"openai", "anthropic"}:
            raise ValueError("provider must be 'openai', 'anthropic' or None")


@dataclass(frozen=True)
class DoctorCheck:
    """One user-facing setup check."""

    key: str
    label: str
    status: CheckStatus
    message_ru: str
    blocker: bool = False


@dataclass(frozen=True)
class DoctorReport:
    """Aggregated doctor result."""

    checks: tuple[DoctorCheck, ...]

    @property
    def has_blockers(self) -> bool:
        return any(check.blocker for check in self.checks)

    @property
    def exit_code(self) -> int:
        return 1 if self.has_blockers else 0


async def run_doctor(
    settings: DoctorSettings,
    *,
    browser_smoke_runner: Callable[[AppConfig, Path], object] | None = None,
    command_runner: CommandRunner | None = None,
) -> DoctorReport:
    """Run all local setup checks."""

    runner = command_runner or _run_command
    cwd = settings.cwd or Path.cwd()
    env_file = _resolve_env_file(settings.env_file, cwd)
    checks: list[DoctorCheck] = [
        check_python_version(),
        check_package_import(),
        check_playwright_installed(),
        check_architecture_boundaries(cwd=cwd),
        check_env_file(settings.env_file, cwd=cwd),
    ]

    config = _load_config(env_file)
    if isinstance(config, DoctorCheck):
        checks.append(config)
        checks.append(check_git_status(cwd=cwd, command_runner=runner))
        return DoctorReport(tuple(checks))

    if settings.provider is not None:
        checks.append(check_provider_key(config, settings.provider))
    checks.append(check_browser_profile(config, cwd=cwd))
    checks.append(check_reports_tmp_path(config, cwd=cwd))
    checks.append(check_git_status(cwd=cwd, command_runner=runner))

    smoke_runner = browser_smoke_runner or check_chromium_launch
    smoke_result = smoke_runner(config, cwd)
    if hasattr(smoke_result, "__await__"):
        smoke_result = await smoke_result  # type: ignore[assignment]
    checks.append(smoke_result)  # type: ignore[arg-type]

    return DoctorReport(tuple(checks))


def check_python_version(
    *,
    min_version: tuple[int, int] = (3, 11),
    version_info: tuple[int, int, int] | None = None,
) -> DoctorCheck:
    """Check the running Python version."""

    version = version_info or (
        sys.version_info.major,
        sys.version_info.minor,
        sys.version_info.micro,
    )
    required = f"{min_version[0]}.{min_version[1]}+"
    current = ".".join(str(part) for part in version[:3])
    if version[:2] < min_version:
        return DoctorCheck(
            key="python",
            label="Python",
            status="failed",
            blocker=True,
            message_ru=f"найден {current}, нужен Python {required}.",
        )
    return DoctorCheck(
        key="python",
        label="Python",
        status="ok",
        message_ru=f"{current} подходит, требуется {required}.",
    )


def check_package_import(package_name: str = "scout_pilot") -> DoctorCheck:
    """Check that the installed package can be imported."""

    try:
        importlib.import_module(package_name)
    except Exception:
        return DoctorCheck(
            key="package_import",
            label="Пакет",
            status="failed",
            blocker=True,
            message_ru=(
                f"не удалось импортировать {package_name}. "
                "Переустановите проект командой `python -m pip install -e .`."
            ),
        )
    return DoctorCheck(
        key="package_import",
        label="Пакет",
        status="ok",
        message_ru=f"{package_name} импортируется.",
    )


def check_playwright_installed() -> DoctorCheck:
    """Check that Playwright package metadata is present without importing its API."""

    if importlib.util.find_spec("playwright") is None:
        return DoctorCheck(
            key="playwright_package",
            label="Playwright",
            status="failed",
            blocker=True,
            message_ru=(
                "пакет не найден. Установите зависимости командой `python -m pip install -e .`."
            ),
        )
    try:
        version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        version = "версия не определена"
    return DoctorCheck(
        key="playwright_package",
        label="Playwright",
        status="ok",
        message_ru=f"пакет установлен ({version}).",
    )


def check_architecture_boundaries(*, cwd: Path = Path.cwd()) -> DoctorCheck:
    """Verify the source boundaries that are important for the assignment."""

    source_root = cwd / "src" / "scout_pilot"
    if not source_root.exists():
        return DoctorCheck(
            key="architecture_boundaries",
            label="Архитектурные границы",
            status="warning",
            message_ru="папка src/scout_pilot не найдена; проверка пропущена.",
        )

    violations: list[str] = []
    core_site_neutral_layers = {
        "llm",
        "navigation",
        "observation",
        "planning",
        "runtime",
        "security",
        "tools",
    }
    for path in source_root.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        lowered = content.casefold()
        relative = path.relative_to(source_root)
        top_layer = relative.parts[0]
        try:
            tree = ast.parse(content, filename=str(relative))
        except SyntaxError as exc:
            violations.append(f"parse error in {relative.as_posix()}: {exc.msg}")
            continue
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        if any(module.startswith("playwright") for module in imported_modules) and (
            top_layer != "browser"
        ):
            violations.append(f"Playwright вне Browser Engine: {relative.as_posix()}")
        if (
            any(
                module == "openai"
                or module.startswith("openai.")
                or module == "anthropic"
                or module.startswith("anthropic.")
                for module in imported_modules
            )
            and top_layer != "llm"
        ):
            violations.append(f"provider SDK вне LLM Layer: {relative.as_posix()}")
        forbidden_html_calls = {"content", "inner" + "_html", "outer" + "_html"}
        raw_html_call = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden_html_calls
            for node in ast.walk(tree)
        )
        raw_html_script = relative.as_posix() != "cli/doctor.py" and any(
            marker in content for marker in ("inner" + "HTML", "outer" + "HTML")
        )
        if raw_html_call or raw_html_script:
            violations.append(f"полный HTML API: {relative.as_posix()}")
        if top_layer in core_site_neutral_layers and any(
            marker in lowered for marker in ("hh.ru", "data-qa=")
        ):
            violations.append(f"site-specific логика: {relative.as_posix()}")

    if violations:
        return DoctorCheck(
            key="architecture_boundaries",
            label="Архитектурные границы",
            status="failed",
            blocker=True,
            message_ru="; ".join(violations[:3]),
        )
    return DoctorCheck(
        key="architecture_boundaries",
        label="Архитектурные границы",
        status="ok",
        message_ru=(
            "Playwright изолирован в Browser Engine; SDK провайдеров — в LLM Layer; "
            "полный HTML и HH.ru-специфичная логика в независимых слоях не найдены."
        ),
    )


async def check_chromium_launch(config: AppConfig, cwd: Path) -> DoctorCheck:
    """Launch Chromium through Browser Engine in headless smoke mode."""

    from scout_pilot.browser import (
        BrowserEngineConfig,
        BrowserEngineError,
        PlaywrightBrowserEngine,
    )

    smoke_profile_dir = cwd / config.reports_dir / "tmp" / "doctor-browser-profile"
    settings = replace(
        BrowserEngineConfig.from_app_config(config),
        user_data_dir=smoke_profile_dir,
        headless=True,
        slow_mo_ms=0,
    )
    engine = PlaywrightBrowserEngine(settings)
    try:
        await engine.start()
    except BrowserEngineError:
        return DoctorCheck(
            key="chromium_launch",
            label="Chromium",
            status="failed",
            blocker=True,
            message_ru=(
                "не удалось запустить headless smoke через Browser Engine. "
                "Проверьте `python -m playwright install chromium`."
            ),
        )
    except Exception:
        return DoctorCheck(
            key="chromium_launch",
            label="Chromium",
            status="failed",
            blocker=True,
            message_ru=(
                "headless smoke завершился неожиданной ошибкой. "
                "Запустите `scout-pilot browser-smoke --headless --hold-seconds 0` для деталей."
            ),
        )
    finally:
        await engine.stop()

    return DoctorCheck(
        key="chromium_launch",
        label="Chromium",
        status="ok",
        message_ru="headless smoke запустился и закрылся через Browser Engine.",
    )


def check_env_file(env_file: Path | None, *, cwd: Path = Path.cwd()) -> DoctorCheck:
    """Check whether the local .env file exists."""

    if env_file is None:
        return DoctorCheck(
            key="env_file",
            label=".env",
            status="warning",
            message_ru="проверка файла отключена для этого запуска.",
        )
    path = _absolute(env_file, cwd)
    if path.exists():
        return DoctorCheck(
            key="env_file",
            label=".env",
            status="ok",
            message_ru=f"файл найден: {_display_path(path, cwd)}.",
        )
    return DoctorCheck(
        key="env_file",
        label=".env",
        status="warning",
        message_ru=(
            "файл не найден. Это не мешает mock/demo режимам, но live OpenAI/Anthropic "
            "потребуют локальные ключи."
        ),
    )


def check_provider_key(config: AppConfig, provider: str) -> DoctorCheck:
    """Check provider key presence only when explicitly requested."""

    has_key = (
        config.provider_secrets.has_openai_key
        if provider == "openai"
        else config.provider_secrets.has_anthropic_key
    )
    variable = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    if has_key:
        return DoctorCheck(
            key="provider_key",
            label="LLM ключ",
            status="ok",
            message_ru=f"{variable} найден в локальной конфигурации.",
        )
    return DoctorCheck(
        key="provider_key",
        label="LLM ключ",
        status="failed",
        blocker=True,
        message_ru=(
            f"{variable} не найден. Для проверки {provider} добавьте ключ только в локальный .env "
            "или запустите doctor без `--provider`."
        ),
    )


def check_browser_profile(config: AppConfig, *, cwd: Path = Path.cwd()) -> DoctorCheck:
    """Check persistent browser profile path and Git ignore status."""

    profile = inspect_browser_profile(config, cwd=cwd)
    display_path = _display_path(_absolute(profile.path, cwd), cwd)
    exists_text = "существует" if profile.exists else "пока не создан, будет создан при запуске"
    git_ignored = _path_or_child_ignored_by_git(profile.path, cwd=cwd)
    if git_ignored is True:
        return DoctorCheck(
            key="browser_profile",
            label="Профиль браузера",
            status="ok",
            message_ru=f"{display_path}: {exists_text}; путь игнорируется Git.",
        )
    if git_ignored is False:
        return DoctorCheck(
            key="browser_profile",
            label="Профиль браузера",
            status="failed",
            blocker=True,
            message_ru=(
                f"{display_path}: путь не закрыт .gitignore. "
                "Добавьте browser profile в ignore перед live demo."
            ),
        )
    return DoctorCheck(
        key="browser_profile",
        label="Профиль браузера",
        status="warning",
        message_ru=(
            f"{display_path}: {exists_text}; Git ignore не удалось проверить. "
            "Не коммитьте cookies, токены и состояние авторизации."
        ),
    )


def check_reports_tmp_path(config: AppConfig, *, cwd: Path = Path.cwd()) -> DoctorCheck:
    """Check that temporary reports are ignored by Git."""

    path = config.reports_dir / "tmp"
    absolute_path = _absolute(path, cwd)
    display_path = _display_path(absolute_path, cwd)
    ignored = _path_or_child_ignored_by_git(path, cwd=cwd)
    exists_text = "существует" if absolute_path.exists() else "пока не создан"
    if ignored is True:
        return DoctorCheck(
            key="reports_tmp",
            label="Временные отчеты",
            status="ok",
            message_ru=f"{display_path}: {exists_text}; путь игнорируется Git.",
        )
    if ignored is False:
        return DoctorCheck(
            key="reports_tmp",
            label="Временные отчеты",
            status="failed",
            blocker=True,
            message_ru=(
                f"{display_path}: путь не закрыт .gitignore. "
                "Report/replay временного demo не должны случайно попасть в коммит."
            ),
        )
    return DoctorCheck(
        key="reports_tmp",
        label="Временные отчеты",
        status="warning",
        message_ru=f"{display_path}: {exists_text}; Git ignore не удалось проверить.",
    )


def check_git_status(
    *,
    cwd: Path = Path.cwd(),
    command_runner: CommandRunner | None = None,
) -> DoctorCheck:
    """Check Git repository and working tree status."""

    runner = command_runner or _run_command
    root_result = runner(["git", "rev-parse", "--show-toplevel"], cwd)
    if root_result.returncode != 0:
        return DoctorCheck(
            key="git_status",
            label="Git",
            status="warning",
            message_ru="репозиторий не найден или Git недоступен; проверка working tree пропущена.",
        )

    root = Path(root_result.stdout.strip() or cwd).resolve()
    status_result = runner(["git", "status", "--short"], root)
    if status_result.returncode != 0:
        return DoctorCheck(
            key="git_status",
            label="Git",
            status="warning",
            message_ru="не удалось получить `git status --short`.",
        )

    changed_lines = [line for line in status_result.stdout.splitlines() if line.strip()]
    if not changed_lines:
        return DoctorCheck(
            key="git_status",
            label="Git",
            status="ok",
            message_ru=f"working tree чистый ({_display_path(root, cwd)}).",
        )

    preview = "; ".join(changed_lines[:3])
    extra = "" if len(changed_lines) <= 3 else f"; еще {len(changed_lines) - 3}"
    return DoctorCheck(
        key="git_status",
        label="Git",
        status="warning",
        message_ru=f"есть несохраненные изменения: {preview}{extra}. Для doctor это не блокер.",
    )


def format_doctor_report(report: DoctorReport) -> tuple[str, ...]:
    """Return Russian CLI lines for a doctor report."""

    lines = ["Проверяю локальную среду Scout Pilot..."]
    for check in report.checks:
        lines.append(f"[{_status_label(check.status)}] {check.label}: {check.message_ru}")
    if report.has_blockers:
        lines.append(
            "Итог: есть блокеры для demo. Исправьте строки с [ОШИБКА] и повторите `scout-pilot doctor`."
        )
    else:
        lines.append(
            "Итог: критических блокеров нет. Предупреждения можно разобрать перед записью demo."
        )
    return tuple(lines)


def _load_config(env_file: Path | None) -> AppConfig | DoctorCheck:
    try:
        return AppConfig.load(env_file=env_file)
    except Exception:
        return DoctorCheck(
            key="config",
            label="Конфигурация",
            status="failed",
            blocker=True,
            message_ru=(
                "не удалось прочитать настройки. Проверьте формат .env и числовые значения таймаутов."
            ),
        )


def _resolve_env_file(env_file: Path | None, cwd: Path) -> Path | None:
    if env_file is None or env_file.is_absolute():
        return env_file
    return cwd / env_file


def _path_or_child_ignored_by_git(path: Path, *, cwd: Path) -> bool | None:
    ignored = is_ignored_by_git(path, cwd=cwd)
    if ignored is True:
        return True
    child_ignored = is_ignored_by_git(path / ".doctor-ignore-check", cwd=cwd)
    if child_ignored is True:
        return True
    return ignored if ignored is not False else child_ignored


def _status_label(status: CheckStatus) -> str:
    return {
        "ok": "OK",
        "warning": "ВНИМАНИЕ",
        "failed": "ОШИБКА",
    }.get(status, status.upper())


def _run_command(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _absolute(path: Path, cwd: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (cwd / path).resolve()


def _display_path(path: Path, cwd: Path) -> str:
    try:
        return path.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
