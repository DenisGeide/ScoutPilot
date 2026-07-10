from pathlib import Path

from scout_pilot.config import AppConfig


def test_config_loads_defaults_without_env_file(tmp_path):
    missing_env = tmp_path / ".env"

    config = AppConfig.load(env_file=missing_env, environ={})

    assert config.environment == "development"
    assert config.browser_profile_dir == Path(".browser-profiles/default")
    assert config.browser_headless is False
    assert config.browser_default_timeout_ms == 10000
    assert config.browser_navigation_timeout_ms == 15000
    assert config.browser_screenshots_dir == Path("reports/tmp/screenshots")
    assert config.llm_timeout_seconds == 30.0
    assert config.llm_max_output_tokens == 1200
    assert config.observation_max_sections == 12
    assert config.observation_max_interactive_elements == 40
    assert config.observation_max_form_fields == 25
    assert config.observation_max_dialogs == 5
    assert config.observation_max_section_chars == 700
    assert config.observation_max_total_chars == 6000
    assert config.require_confirmation is True
    assert config.max_context_tokens == 12000


def test_config_reads_env_file_and_hides_secrets(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SCOUT_PILOT_ENV=test",
                "SCOUT_PILOT_REQUIRE_CONFIRMATION=false",
                "SCOUT_PILOT_BROWSER_HEADLESS=true",
                "SCOUT_PILOT_BROWSER_DEFAULT_TIMEOUT_MS=3000",
                "SCOUT_PILOT_BROWSER_NAVIGATION_TIMEOUT_MS=4000",
                "SCOUT_PILOT_BROWSER_SCREENSHOTS_DIR=reports/tmp/browser",
                "SCOUT_PILOT_LLM_TIMEOUT_SECONDS=12.5",
                "SCOUT_PILOT_LLM_MAX_OUTPUT_TOKENS=333",
                "SCOUT_PILOT_OBSERVATION_MAX_SECTIONS=3",
                "SCOUT_PILOT_OBSERVATION_MAX_INTERACTIVE_ELEMENTS=4",
                "SCOUT_PILOT_OBSERVATION_MAX_FORM_FIELDS=5",
                "SCOUT_PILOT_OBSERVATION_MAX_DIALOGS=2",
                "SCOUT_PILOT_OBSERVATION_MAX_SECTION_CHARS=300",
                "SCOUT_PILOT_OBSERVATION_MAX_TOTAL_CHARS=2000",
                "SCOUT_PILOT_MAX_CONTEXT_TOKENS=2048",
                "OPENAI_API_KEY=secret-value",
            ]
        ),
        encoding="utf-8",
    )

    config = AppConfig.load(env_file=env_file, environ={})

    assert config.environment == "test"
    assert config.require_confirmation is False
    assert config.browser_headless is True
    assert config.browser_default_timeout_ms == 3000
    assert config.browser_navigation_timeout_ms == 4000
    assert config.browser_screenshots_dir == Path("reports/tmp/browser")
    assert config.llm_timeout_seconds == 12.5
    assert config.llm_max_output_tokens == 333
    assert config.observation_max_sections == 3
    assert config.observation_max_interactive_elements == 4
    assert config.observation_max_form_fields == 5
    assert config.observation_max_dialogs == 2
    assert config.observation_max_section_chars == 300
    assert config.observation_max_total_chars == 2000
    assert config.max_context_tokens == 2048
    assert config.provider_secrets.has_openai_key is True
    assert "secret-value" not in repr(config)
