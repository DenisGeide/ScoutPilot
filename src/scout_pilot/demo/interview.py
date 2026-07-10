"""Interview demonstration mode built on generic browser-agent layers."""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.config import AppConfig
from scout_pilot.demo.vacancy_search import (
    VacancySearchDemoRunner,
    VacancySearchDemoSettings,
)
from scout_pilot.observation import ObservationSettings, SemanticObservationEngine
from scout_pilot.tools import DefaultToolRuntime, ToolContext, create_browser_tool_registry


ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class InterviewDemoSettings:
    """Settings for the deterministic local interview demonstration."""

    site_dir: Path = Path("reports/tmp/interview-demo-site")
    profile_dir: Path = Path(".browser-profiles/interview-demo")
    report_path: Path = Path("reports/tmp/interview-demo-report.json")
    replay_path: Path = Path("reports/tmp/interview-demo-replay.json")
    query: str = "AI Engineer Python AI Developer"
    max_vacancies: int = 3
    headless: bool = False
    slow_mo_ms: int = 80
    wait_after_search_ms: int = 200

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query cannot be empty")
        if self.max_vacancies <= 0:
            raise ValueError("max_vacancies must be positive")
        if self.slow_mo_ms < 0:
            raise ValueError("slow_mo_ms cannot be negative")
        if self.wait_after_search_ms < 0:
            raise ValueError("wait_after_search_ms cannot be negative")


@dataclass(frozen=True)
class InterviewDemoResult:
    """Outcome returned by the local interview demonstration."""

    success: bool
    message_ru: str
    local_site_url: str
    report_path: Path
    replay_path: Path
    notes_count: int
    security_pause_count: int


@dataclass(frozen=True)
class LocalInterviewSite:
    """Generated local test site metadata."""

    root: Path
    start_page_name: str = "index.html"


async def run_local_interview_demo(
    config: AppConfig,
    settings: InterviewDemoSettings,
    *,
    progress: ProgressCallback | None = None,
) -> InterviewDemoResult:
    """Run the deterministic local demo without live websites or credentials."""

    site = prepare_local_interview_site(settings.site_dir)

    def emit(message_ru: str) -> None:
        if progress is not None:
            progress(message_ru)

    emit("Готовлю локальный тестовый сайт для демо интервью.")
    emit(f"Постоянный профиль браузера настроен: {settings.profile_dir}. Путь исключен из Git.")

    with LocalDemoServer(site.root) as server:
        start_url = server.url_for(site.start_page_name)
        emit(f"Локальный сайт запущен: {server.base_url}.")
        emit("Запускаю общий браузерный сценарий. В демо нет live LLM-вызовов и реальных откликов.")

        browser_settings = replace(
            BrowserEngineConfig.from_app_config(config),
            user_data_dir=settings.profile_dir,
            headless=settings.headless,
            slow_mo_ms=settings.slow_mo_ms,
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
        result = await runner.run(
            VacancySearchDemoSettings(
                start_url=start_url,
                query=settings.query,
                max_vacancies=settings.max_vacancies,
                report_path=settings.report_path,
                replay_path=settings.replay_path,
                confirm_search_fill=True,
                confirm_search_submit=False,
                probe_security=True,
                wait_after_search_ms=settings.wait_after_search_ms,
            ),
            progress=emit,
        )

    emit(
        "Локальное демо записало семантические наблюдения, решения по инструментам, "
        "метрики бюджета контекста и паузу безопасности в JSON-артефакты."
    )
    return InterviewDemoResult(
        success=result.success,
        message_ru=result.message_ru,
        local_site_url=start_url,
        report_path=result.report_path,
        replay_path=result.replay_path or settings.replay_path,
        notes_count=len(result.notes),
        security_pause_count=len(result.security_pauses),
    )


def prepare_local_interview_site(root: Path) -> LocalInterviewSite:
    """Create deterministic local test pages used by the interview demo."""

    root.mkdir(parents=True, exist_ok=True)
    pages = (
        (
            "role-alpha.html",
            "AI Engineer - Evaluation Platform",
            (
                "Requirements: Python, model evaluation, LLM tooling, async APIs, "
                "and careful product communication."
            ),
        ),
        (
            "role-beta.html",
            "Python AI Developer - Automation",
            (
                "Required skills include Python, browser automation, test design, "
                "LLM integrations, and reliable service boundaries."
            ),
        ),
        (
            "role-gamma.html",
            "Applied LLM Engineer",
            (
                "Experience with retrieval systems, prompt evaluation, data pipelines, "
                "Python, and production monitoring is required."
            ),
        ),
    )
    for file_name, title, body in pages:
        _write_role_page(root / file_name, title, body)
    _write_index_page(root / "index.html", pages)
    return LocalInterviewSite(root=root)


class LocalDemoServer:
    """Small local HTTP server for deterministic local test pages."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self.base_url = ""

    def __enter__(self) -> "LocalDemoServer":
        handler = partial(_QuietHandler, directory=str(self._root))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        host, port = self._server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def url_for(self, page_name: str) -> str:
        return f"{self.base_url}/{page_name}"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return None


def _write_index_page(root_page: Path, pages: tuple[tuple[str, str, str], ...]) -> None:
    links = "\n".join(
        (
            f'<article class="result-card">'
            f"<h2>{html.escape(title)}</h2>"
            f'<a href="{html.escape(file_name)}">'
            f"{html.escape(title)}"
            f"</a>"
            f"<p>{html.escape(body)}</p>"
            f"</article>"
        )
        for file_name, title, body in pages
    )
    root_page.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Scout Pilot Interview Demo</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #1f2933; }}
    header, main, footer {{ padding: 24px 32px; }}
    header {{ background: #eef5f1; border-bottom: 1px solid #c9d8d0; }}
    nav a {{ margin-right: 16px; color: #375f4d; }}
    label {{ display: block; font-weight: 700; margin-bottom: 8px; }}
    input {{ min-width: 360px; padding: 10px; border: 1px solid #8aa096; }}
    button {{ padding: 10px 14px; margin-left: 8px; }}
    .result-card {{ border-top: 1px solid #d8e0dc; padding: 18px 0; }}
    .status {{ margin-top: 12px; color: #4b5c54; }}
  </style>
</head>
<body>
  <header>
    <nav aria-label="Primary">
      <a href="index.html">Open roles</a>
      <a href="index.html#about">About demo</a>
    </nav>
    <h1>Engineering roles</h1>
  </header>
  <main>
    <section role="search" aria-labelledby="search-title">
      <h2 id="search-title">Search open roles</h2>
      <label for="role-query">Search vacancies</label>
      <input id="role-query" name="query" type="search" placeholder="AI, Python, LLM">
      <button type="button" onclick="showResults()">Search</button>
      <p id="search-status" class="status">Results are hidden until search runs.</p>
    </section>
    <section id="results" hidden aria-label="Search results">
      <h2>Matching roles</h2>
      {links}
    </section>
  </main>
  <footer id="about">
    <p>This local page is deterministic and contains no external accounts or credentials.</p>
  </footer>
  <script>
    function showResults() {{
      document.title = "Scout Pilot Search Results";
      document.getElementById("results").hidden = false;
      document.getElementById("search-status").textContent = "Three matching roles are visible.";
    }}
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def _write_role_page(path: Path, title: str, body: str) -> None:
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #1f2933; }}
    header, main {{ padding: 24px 32px; }}
    header {{ background: #eef5f1; border-bottom: 1px solid #c9d8d0; }}
    article {{ max-width: 820px; }}
    button {{ padding: 10px 14px; margin-top: 16px; }}
  </style>
</head>
<body>
  <header>
    <a href="index.html">Back to roles</a>
    <h1>{html.escape(title)}</h1>
  </header>
  <main>
    <article>
      <p>{html.escape(body)}</p>
      <p>Notes: the team values clear ownership, reliable testing, and safe automation.</p>
      <button type="button" aria-label="Apply to role">Apply</button>
    </article>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
