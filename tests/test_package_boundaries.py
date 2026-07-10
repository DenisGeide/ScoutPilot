from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "scout_pilot"


def test_foundation_does_not_import_playwright_or_provider_sdks():
    forbidden_terms = ()

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


def test_only_llm_layer_imports_provider_sdks():
    for path in SOURCE_ROOT.rglob("*.py"):
        content = path.read_text(encoding="utf-8").lower()
        imports_provider = (
            "from openai" in content
            or "import openai" in content
            or "from anthropic" in content
            or "import anthropic" in content
        )
        if imports_provider:
            relative_parts = path.relative_to(SOURCE_ROOT).parts
            assert relative_parts[0] == "llm", f"{path} imports provider SDK outside LLM layer"


def test_source_does_not_use_complete_html_apis():
    forbidden_terms = ("page.content(", "inner_html", "outer_html", "innerHTML", "outerHTML")

    for path in SOURCE_ROOT.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            assert term not in content, f"{path} contains complete HTML access: {term}"
