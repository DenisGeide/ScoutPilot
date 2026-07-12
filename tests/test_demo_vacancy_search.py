import asyncio
import json
from pathlib import Path

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.demo import LocalDemoServer, VacancySearchDemoRunner, VacancySearchDemoSettings
from scout_pilot.observation import ObservationSettings, SemanticObservationEngine
from scout_pilot.tools import DefaultToolRuntime, ToolContext, create_browser_tool_registry


def test_generic_vacancy_demo_reads_three_pages_and_records_security_pause(tmp_path):
    starts = [_write_site_a(tmp_path / "site-a"), _write_site_b(tmp_path / "site-b")]

    for index, start_page in enumerate(starts):
        messages: list[str] = []
        result = _run_demo(
            tmp_path / f"profile-{index}",
            start_page,
            tmp_path / f"report-{index}.json",
            confirm_search_fill=True,
            probe_security=True,
            progress=messages.append,
        )

        assert result.success is True
        assert result.stop_reason == "completed"
        assert len(result.notes) == 3
        assert any(pause["risk"] == "external_side_effect" for pause in result.security_pauses)

        report = json.loads(result.report_path.read_text(encoding="utf-8"))
        serialized = json.dumps(report, ensure_ascii=False).casefold()
        assert report["success"] is True
        assert report["stopped_before_side_effects"] is True
        assert report["start_url"].endswith(start_page.name)
        assert len(report["discovered_urls"]) == 3
        assert len(report["pages_read"]) == 3
        assert report["blockers"] == []
        assert len(report["notes"]) == 3
        assert report["final_notes"] == report["notes"]
        assert report["summary"]["discovered_url_count"] == 3
        assert report["summary"]["pages_read_count"] == 3
        assert report["summary"]["blocker_count"] == 0
        assert any(event["kind"] == "selected_tool" for event in report["events"])
        assert any(event["kind"] == "observation" for event in report["events"])
        assert "Открыл стартовую страницу." in messages
        assert "Нашел поле поиска." in messages
        assert "Запрос требует подтверждения." in messages
        assert "Нашел 3 кандидатов." in messages
        assert "Читаю страницу 1/3." in messages
        assert "Остановился перед внешним действием." in messages
        assert "[redacted]" in serialized
        assert "<html" not in serialized
        assert "<button" not in serialized
        assert "data-applied" not in serialized


def test_demo_search_does_not_require_confirmation(tmp_path):
    start_page = _write_site_a(tmp_path / "site")

    result = _run_demo(
        tmp_path / "profile",
        start_page,
        tmp_path / "report.json",
        confirm_search_fill=False,
        probe_security=False,
    )

    assert result.success is True
    assert result.stop_reason == "completed"
    assert len(result.notes) == 3
    assert result.security_pauses == ()


def test_demo_records_blocker_for_local_hh_like_blocking_page(tmp_path):
    messages: list[str] = []
    start_page = _write_blocked_site(tmp_path / "blocked-site")

    result = _run_demo(
        tmp_path / "profile-blocked",
        start_page,
        tmp_path / "blocked-report.json",
        confirm_search_fill=True,
        probe_security=False,
        progress=messages.append,
    )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    serialized = json.dumps(report, ensure_ascii=False).casefold()

    assert result.success is False
    assert result.stop_reason == "page_not_available"
    assert report["blockers"]
    assert report["summary"]["blocker_count"] >= 1
    assert "captcha_blocking_page" in serialized
    assert "Открыл стартовую страницу." in messages
    assert "<html" not in serialized
    assert "<button" not in serialized


def test_demo_source_has_no_site_specific_routes_or_selectors():
    source_root = Path(__file__).resolve().parents[1] / "src" / "scout_pilot"
    scanned_dirs = ("demo", "runtime", "navigation", "tools")
    forbidden = (
        "hh.ru",
        "data-qa",
        "/vacancy",
        "/jobs",
        "/search",
        "xpath",
        "queryselector",
        "locator(",
    )

    paths = [
        path
        for directory in scanned_dirs
        for path in (source_root / directory).rglob("*.py")
    ]
    for path in paths:
        content = path.read_text(encoding="utf-8").casefold()
        for term in forbidden:
            assert term not in content, f"{path} contains demo-specific logic: {term}"


def _run_demo(
    profile_dir: Path,
    start_page: Path,
    report_path: Path,
    *,
    confirm_search_fill: bool,
    probe_security: bool,
    progress=None,
):
    browser = PlaywrightBrowserEngine(
        BrowserEngineConfig(
            user_data_dir=profile_dir,
            headless=True,
            default_timeout_ms=10000,
            navigation_timeout_ms=10000,
            screenshots_dir=profile_dir / "screenshots",
        )
    )
    observer = SemanticObservationEngine(
        browser,
        ObservationSettings(max_sections=20, max_interactive_elements=60),
    )
    runtime = DefaultToolRuntime(
        create_browser_tool_registry(),
        ToolContext(browser=browser, observation_engine=observer),
    )
    runner = VacancySearchDemoRunner(
        browser=browser,
        observation_engine=observer,
        tool_runtime=runtime,
    )

    async def scenario():
        with LocalDemoServer(start_page.parent) as server:
            return await runner.run(
                VacancySearchDemoSettings(
                    start_url=server.url_for(start_page.name),
                    query="AI Engineer Python AI Developer",
                    max_vacancies=3,
                    report_path=report_path,
                    replay_path=report_path.with_name(f"{report_path.stem}-replay.json"),
                    confirm_search_fill=confirm_search_fill,
                    probe_security=probe_security,
                    wait_after_search_ms=50,
                ),
                progress=progress,
            )

    return asyncio.run(scenario())


def _write_site_a(root: Path) -> Path:
    root.mkdir()
    pages = [
        (
            "ai-engineer.html",
            "AI Engineer - Applied ML",
            "Requirements: Python, machine learning, evaluation pipelines, and LLM tooling.",
        ),
        (
            "python-ai-developer.html",
            "Python AI Developer",
            "Requirements: production Python, async services, prompt evaluation, and APIs.",
        ),
        (
            "llm-engineer.html",
            "LLM Engineer",
            "Requirements: retrieval systems, model monitoring, Python, and ML experiments.",
        ),
    ]
    for file_name, title, body in pages:
        _write_detail(root / file_name, title, body)

    page = root / "index.html"
    page.write_text(
        f"""
        <!doctype html>
        <title>Open roles A</title>
        <main>
          <h1>Open engineering roles</h1>
          <label for="q">Search vacancies</label>
          <input id="q" type="search" name="query" placeholder="Search roles">
          <button type="button" onclick="document.title='Search results A'; document.getElementById('results').hidden=false">Search</button>
          <section id="results" hidden>
            <h2>Results</h2>
            <a href="ai-engineer.html">AI Engineer - Applied ML</a>
            <a href="python-ai-developer.html">Python AI Developer</a>
            <a href="llm-engineer.html">LLM Engineer</a>
          </section>
        </main>
        """,
        encoding="utf-8",
    )
    return page


def _write_site_b(root: Path) -> Path:
    root.mkdir()
    pages = [
        (
            "role-one.html",
            "AI Engineer B",
            "Experience with Python, ML services, LLM quality checks, and data pipelines is required.",
        ),
        (
            "role-two.html",
            "Python AI Developer B",
            "Required skills include Python, API design, model integrations, and test automation.",
        ),
        (
            "role-three.html",
            "Applied LLM Engineer B",
            "The team expects LLM application experience, evaluation, Python, and product thinking.",
        ),
    ]
    for file_name, title, body in pages:
        _write_detail(root / file_name, title, body)

    page = root / "home.html"
    page.write_text(
        f"""
        <!doctype html>
        <title>Open roles B</title>
        <main>
          <section role="search">
            <h1>Team openings</h1>
            <input aria-label="Find roles" name="term" placeholder="Find by skill">
            <input type="button" value="Find" onclick="document.title='Search results B'; document.getElementById('cards').hidden=false">
          </section>
          <section id="cards" hidden>
            <article>
              <h2>AI Engineer B</h2>
              <a href="role-one.html"><strong>AI Engineer B</strong><span>Open role</span></a>
            </article>
            <article>
              <h2>Python AI Developer B</h2>
              <a href="role-two.html"><strong>Python AI Developer B</strong><span>Open role</span></a>
            </article>
            <article>
              <h2>Applied LLM Engineer B</h2>
              <a href="role-three.html"><strong>Applied LLM Engineer B</strong><span>Open role</span></a>
            </article>
          </section>
        </main>
        """,
        encoding="utf-8",
    )
    return page


def _write_blocked_site(root: Path) -> Path:
    root.mkdir()
    page = root / "index.html"
    page.write_text(
        """
        <!doctype html>
        <title>Human verification</title>
        <main>
          <h1>Verify you are human</h1>
          <p>CAPTCHA check is required before browsing this page.</p>
          <button type="button">Continue</button>
        </main>
        """,
        encoding="utf-8",
    )
    return page


def _write_detail(path: Path, title: str, body: str) -> None:
    path.write_text(
        f"""
        <!doctype html>
        <title>{title}</title>
        <main>
          <article>
            <h1>{title}</h1>
            <p>{body}</p>
            <p>Notes: remote-friendly team, product work, and engineering ownership.</p>
            <button type="button" aria-label="Apply to role" onclick="document.body.dataset.applied='true'">Apply</button>
          </article>
        </main>
        """,
        encoding="utf-8",
    )
