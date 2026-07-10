import asyncio
import json
from pathlib import Path

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.demo import VacancySearchDemoRunner, VacancySearchDemoSettings
from scout_pilot.observation import ObservationSettings, SemanticObservationEngine
from scout_pilot.tools import DefaultToolRuntime, ToolContext, create_browser_tool_registry


def test_generic_vacancy_demo_reads_three_pages_and_records_security_pause(tmp_path):
    starts = [_write_site_a(tmp_path / "site-a"), _write_site_b(tmp_path / "site-b")]

    for index, start_page in enumerate(starts):
        result = _run_demo(
            tmp_path / f"profile-{index}",
            start_page,
            tmp_path / f"report-{index}.json",
            confirm_search_fill=True,
            probe_security=True,
        )

        assert result.success is True
        assert result.stop_reason == "completed"
        assert len(result.notes) == 3
        assert any(pause["risk"] == "external_side_effect" for pause in result.security_pauses)

        report = json.loads(result.report_path.read_text(encoding="utf-8"))
        serialized = json.dumps(report, ensure_ascii=False).casefold()
        assert report["success"] is True
        assert report["stopped_before_side_effects"] is True
        assert len(report["notes"]) == 3
        assert any(event["kind"] == "selected_tool" for event in report["events"])
        assert any(event["kind"] == "observation" for event in report["events"])
        assert "[redacted]" in serialized
        assert "<html" not in serialized
        assert "<button" not in serialized
        assert "data-applied" not in serialized


def test_demo_stops_before_unconfirmed_search_fill(tmp_path):
    start_page = _write_site_a(tmp_path / "site")

    result = _run_demo(
        tmp_path / "profile",
        start_page,
        tmp_path / "report.json",
        confirm_search_fill=False,
        probe_security=False,
    )

    assert result.success is False
    assert result.stop_reason == "confirmation_required"
    assert result.notes == ()
    assert result.security_pauses
    assert result.security_pauses[0]["tool_name"] == "browser.fill_by_label"


def test_demo_source_has_no_site_specific_routes_or_selectors():
    source_root = Path(__file__).resolve().parents[1] / "src" / "scout_pilot" / "demo"
    forbidden = ("hh.ru", "/vacancy", "/jobs", "/search", "xpath", "queryselector", "locator(")

    for path in source_root.rglob("*.py"):
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
        return await runner.run(
            VacancySearchDemoSettings(
                start_url=start_page.resolve().as_uri(),
                query="AI Engineer Python AI Developer",
                max_vacancies=3,
                report_path=report_path,
                confirm_search_fill=confirm_search_fill,
                probe_security=probe_security,
                wait_after_search_ms=50,
            )
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
            <a href="{(root / 'ai-engineer.html').resolve().as_uri()}">AI Engineer - Applied ML</a>
            <a href="{(root / 'python-ai-developer.html').resolve().as_uri()}">Python AI Developer</a>
            <a href="{(root / 'llm-engineer.html').resolve().as_uri()}">LLM Engineer</a>
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
              <a href="{(root / 'role-one.html').resolve().as_uri()}"><strong>AI Engineer B</strong><span>Open role</span></a>
            </article>
            <article>
              <h2>Python AI Developer B</h2>
              <a href="{(root / 'role-two.html').resolve().as_uri()}"><strong>Python AI Developer B</strong><span>Open role</span></a>
            </article>
            <article>
              <h2>Applied LLM Engineer B</h2>
              <a href="{(root / 'role-three.html').resolve().as_uri()}"><strong>Applied LLM Engineer B</strong><span>Open role</span></a>
            </article>
          </section>
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
