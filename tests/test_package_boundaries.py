from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "scout_pilot"


def test_foundation_does_not_import_playwright_or_provider_sdks():
    forbidden_terms = (
        "from openai",
        "import openai",
        "from anthropic",
        "import anthropic",
    )

    for path in SOURCE_ROOT.rglob("*.py"):
        content = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term not in content, f"{path} contains forbidden foundation import: {term}"


def test_only_browser_layer_imports_playwright():
    for path in SOURCE_ROOT.rglob("*.py"):
        content = path.read_text(encoding="utf-8").lower()
        imports_playwright = "from playwright" in content or "import playwright" in content
        if imports_playwright:
            relative_parts = path.relative_to(SOURCE_ROOT).parts
            assert relative_parts[0] == "browser", f"{path} imports Playwright outside Browser Engine"


def test_source_does_not_use_complete_html_apis():
    forbidden_terms = ("page.content(", "inner_html", "outer_html", "innerHTML", "outerHTML")

    for path in SOURCE_ROOT.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            assert term not in content, f"{path} contains complete HTML access: {term}"
