"""Local live demo that runs through the autonomous runtime."""

from __future__ import annotations

import html
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from scout_pilot.cli.task_session import CliTaskSettings, run_cli_task
from scout_pilot.config import AppConfig
from scout_pilot.demo.interview import LocalDemoServer


ProgressCallback = Callable[[str], None]

DEFAULT_LIVE_LOCAL_TASK = (
    "Найди три подходящие AI Engineer вакансии, прочитай описания, "
    "сравни требования и остановись перед откликом."
)


@dataclass(frozen=True)
class LiveLocalDemoSettings:
    """Settings for the deterministic local live-runtime demo."""

    site_dir: Path = Path("reports/tmp/live-local-demo-site")
    profile_dir: Path = Path(".browser-profiles/live-local-demo")
    report_path: Path = Path("reports/tmp/live-local-demo-report.json")
    replay_path: Path = Path("reports/tmp/live-local-demo-replay.json")
    task: str = DEFAULT_LIVE_LOCAL_TASK
    provider: str = "mock"
    dashboard: str = "compact"
    max_iterations: int = 8
    headless: bool = False
    slow_mo_ms: int = 80

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("task cannot be empty")
        if self.provider not in {"mock", "openai", "anthropic", "codex"}:
            raise ValueError("provider must be 'mock', 'openai', 'anthropic' or 'codex'")
        if self.dashboard not in {"compact", "verbose", "off"}:
            raise ValueError("dashboard must be 'compact', 'verbose' or 'off'")
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if self.slow_mo_ms < 0:
            raise ValueError("slow_mo_ms cannot be negative")


@dataclass(frozen=True)
class LiveLocalDemoResult:
    """Summary returned after the live local runtime demo."""

    success: bool
    message_ru: str
    local_site_url: str
    report_path: Path
    replay_path: Path
    detail_pages_read: int
    security_pause_count: int
    ambiguity_checks: int


@dataclass(frozen=True)
class LocalLiveRuntimeSite:
    """Generated local site metadata."""

    root: Path
    start_page_name: str = "index.html"


async def run_live_local_demo(
    config: AppConfig,
    settings: LiveLocalDemoSettings,
    *,
    progress: ProgressCallback | None = None,
) -> LiveLocalDemoResult:
    """Run a deterministic local site through the normal live CLI runtime path."""

    site = prepare_live_local_demo_site(settings.site_dir)

    def emit(message_ru: str) -> None:
        if progress is not None:
            progress(message_ru)

    emit("Готовлю локальный сайт для live-demo через обычный runtime.")
    emit(f"Профиль браузера для демо: {settings.profile_dir}. Путь исключен из Git.")

    with LocalDemoServer(site.root) as server:
        start_url = server.url_for(site.start_page_name)
        emit(f"Локальный сайт запущен: {server.base_url}.")
        emit("Запускаю scout-pilot run --live на локальном сайте.")
        cli_result = await run_cli_task(
            CliTaskSettings(
                task=settings.task,
                dry_run=False,
                report_path=settings.report_path,
                replay_path=settings.replay_path,
                dashboard=settings.dashboard,
                start_url=start_url,
                provider=settings.provider,
                max_iterations=settings.max_iterations,
                headless=settings.headless,
                browser_profile_dir=settings.profile_dir,
                slow_mo_ms=settings.slow_mo_ms,
                mock_provider_mode=(
                    "live_local_demo" if settings.provider == "mock" else "default"
                ),
            ),
            progress=emit,
        )

    summary = _summarize_runtime_report(settings.report_path)
    expected_pause = summary["detail_pages_read"] >= 3 and summary["security_pause_count"] >= 1
    success = bool(cli_result.success or expected_pause)
    if expected_pause:
        message_ru = (
            "Live local demo дошло до ожидаемой паузы безопасности: агент прочитал "
            "три страницы деталей, подготовил сравнение и остановился перед Apply."
        )
    else:
        message_ru = (
            "Live local demo завершилось без ожидаемой паузы перед Apply. "
            "Проверьте report/replay и вывод терминала."
        )
    emit(message_ru)

    return LiveLocalDemoResult(
        success=success,
        message_ru=message_ru,
        local_site_url=start_url,
        report_path=settings.report_path,
        replay_path=settings.replay_path,
        detail_pages_read=int(summary["detail_pages_read"]),
        security_pause_count=int(summary["security_pause_count"]),
        ambiguity_checks=int(summary["ambiguity_checks"]),
    )


def prepare_live_local_demo_site(root: Path) -> LocalLiveRuntimeSite:
    """Create a deterministic local site for the runtime-driven demo."""

    root.mkdir(parents=True, exist_ok=True)
    pages = (
        (
            "detail-alpha.html",
            "AI Engineer - Evaluation Platform",
            "Requirements: Python, LLM evaluation, async APIs, browser automation, and testing.",
        ),
        (
            "detail-beta.html",
            "Python AI Developer - Workflow Automation",
            "Requirements: production Python, semantic automation, provider integrations, and reliability.",
        ),
        (
            "detail-gamma.html",
            "Applied LLM Engineer - Search Quality",
            "Requirements: retrieval, prompt evaluation, monitoring, Python, and clear product writing.",
        ),
    )
    for file_name, title, body in pages:
        _write_detail_page(root / file_name, title, body)
    _write_search_page(root / "index.html", pages)
    return LocalLiveRuntimeSite(root=root)


def _write_search_page(root_page: Path, pages: tuple[tuple[str, str, str], ...]) -> None:
    cards = "\n".join(
        (
            "<article>"
            f"<h2>{html.escape(title)}</h2>"
            f"<p>{html.escape(body)}</p>"
            f'<a href="{html.escape(file_name)}">Details</a>'
            "</article>"
        )
        for file_name, title, body in pages
    )
    root_page.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Local AI Roles</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #202a32; }}
    header, main, footer {{ padding: 24px 32px; }}
    header {{ background: #eef5f1; border-bottom: 1px solid #cbd8d1; }}
    article {{ border-top: 1px solid #d8e0dc; padding: 18px 0; max-width: 820px; }}
    button {{ padding: 10px 14px; }}
    a {{ color: #255d4d; font-weight: 700; }}
  </style>
</head>
<body>
  <header>
    <h1>Local AI roles</h1>
    <p>Deterministic local page for semantic browser-agent testing.</p>
  </header>
  <main>
    <section aria-labelledby="search-heading">
      <h2 id="search-heading">Search open roles</h2>
      <p>Use the button to show matching AI Engineer roles.</p>
      <button type="button" onclick="showMatches()">Show matches</button>
      <p id="search-status">No results are visible yet.</p>
    </section>
    <section id="results" hidden aria-label="Matching roles">
      <h2>Matching AI Engineer roles</h2>
      {cards}
    </section>
  </main>
  <footer>
    <p>No external account or credentials are required.</p>
  </footer>
  <script>
    function showMatches() {{
      document.title = "Local AI Role Matches";
      document.getElementById("results").hidden = false;
      document.getElementById("search-status").textContent = "Three matching roles are visible.";
    }}
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def _write_detail_page(path: Path, title: str, body: str) -> None:
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #202a32; }}
    header, main {{ padding: 24px 32px; }}
    header {{ background: #eef5f1; border-bottom: 1px solid #cbd8d1; }}
    article {{ max-width: 820px; }}
    button {{ padding: 10px 14px; margin-top: 16px; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <p>Local detail page generated for the live runtime demo.</p>
  </header>
  <main>
    <article>
      <p>{html.escape(body)}</p>
      <p>Team notes: careful automation, readable tests, and clear communication matter.</p>
      <button type="button" aria-label="Apply">Apply</button>
    </article>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )


def _summarize_runtime_report(report_path: Path) -> Mapping[str, int]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    events = report.get("events") if isinstance(report, Mapping) else []
    if not isinstance(events, list):
        events = []

    search_page_titles = {"Local AI Roles", "Local AI Role Matches"}
    detail_titles: set[str] = set()
    security_pause_count = 0
    ambiguity_checks = 0
    for event in events:
        if not isinstance(event, Mapping):
            continue
        details = event.get("details")
        if not isinstance(details, Mapping):
            continue
        name = event.get("name")
        title = details.get("title")
        if (
            name in {"observation_captured", "post_action_observation_captured"}
            and isinstance(title, str)
            and title
            and title not in search_page_titles
        ):
            detail_titles.add(title)
        if name == "tool_execution_finished" and details.get("tool_status") == "paused":
            security_pause_count += 1
        selected_tool = details.get("selected_tool") or details.get("tool_name")
        if (
            name == "tool_execution_finished"
            and selected_tool == "browser.resolve_target"
        ):
            ambiguity_checks += 1
    return {
        "detail_pages_read": len(detail_titles),
        "security_pause_count": security_pause_count,
        "ambiguity_checks": ambiguity_checks,
    }
