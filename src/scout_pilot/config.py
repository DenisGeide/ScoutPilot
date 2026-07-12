"""Application configuration loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
import os


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


@dataclass(frozen=True)
class ProviderSecrets:
    """Secret provider settings kept out of object representation."""

    openai_api_key: str | None = field(default=None, repr=False)
    anthropic_api_key: str | None = field(default=None, repr=False)

    @property
    def has_openai_key(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key)


@dataclass(frozen=True)
class AppConfig:
    """Runtime settings loaded from environment variables and optional .env files."""

    environment: str = "development"
    log_level: str = "INFO"
    browser_profile_dir: Path = Path(".browser-profiles/default")
    browser_headless: bool = False
    browser_default_timeout_ms: int = 10000
    browser_navigation_timeout_ms: int = 15000
    browser_screenshots_dir: Path = Path("reports/tmp/screenshots")
    browser_viewport_width: int = 1000
    browser_viewport_height: int = 900
    observation_max_sections: int = 12
    observation_max_interactive_elements: int = 60
    observation_max_form_fields: int = 25
    observation_max_dialogs: int = 5
    observation_max_section_chars: int = 700
    observation_max_total_chars: int = 12000
    reports_dir: Path = Path("reports")
    llm_provider: str = "openai"
    llm_model: str = "gpt-4.1-mini"
    llm_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 1200
    require_confirmation: bool = True
    max_context_tokens: int = 12000
    provider_secrets: ProviderSecrets = field(default_factory=ProviderSecrets)

    @classmethod
    def load(
        cls,
        env_file: Path | None = Path(".env"),
        environ: Mapping[str, str] | None = None,
    ) -> "AppConfig":
        values: dict[str, str] = {}
        if env_file is not None:
            values.update(_read_env_file(env_file))
        values.update(dict(os.environ if environ is None else environ))

        return cls(
            environment=values.get("SCOUT_PILOT_ENV", "development"),
            log_level=values.get("SCOUT_PILOT_LOG_LEVEL", "INFO").upper(),
            browser_profile_dir=Path(
                values.get("SCOUT_PILOT_BROWSER_PROFILE_DIR", ".browser-profiles/default")
            ),
            browser_headless=_parse_bool(values.get("SCOUT_PILOT_BROWSER_HEADLESS", "false")),
            browser_default_timeout_ms=_parse_positive_int(
                values.get("SCOUT_PILOT_BROWSER_DEFAULT_TIMEOUT_MS", "10000"),
                variable_name="SCOUT_PILOT_BROWSER_DEFAULT_TIMEOUT_MS",
            ),
            browser_navigation_timeout_ms=_parse_positive_int(
                values.get("SCOUT_PILOT_BROWSER_NAVIGATION_TIMEOUT_MS", "15000"),
                variable_name="SCOUT_PILOT_BROWSER_NAVIGATION_TIMEOUT_MS",
            ),
            browser_screenshots_dir=Path(
                values.get("SCOUT_PILOT_BROWSER_SCREENSHOTS_DIR", "reports/tmp/screenshots")
            ),
            browser_viewport_width=_parse_positive_int(
                values.get("SCOUT_PILOT_BROWSER_VIEWPORT_WIDTH", "1000"),
                variable_name="SCOUT_PILOT_BROWSER_VIEWPORT_WIDTH",
            ),
            browser_viewport_height=_parse_positive_int(
                values.get("SCOUT_PILOT_BROWSER_VIEWPORT_HEIGHT", "900"),
                variable_name="SCOUT_PILOT_BROWSER_VIEWPORT_HEIGHT",
            ),
            observation_max_sections=_parse_positive_int(
                values.get("SCOUT_PILOT_OBSERVATION_MAX_SECTIONS", "12"),
                variable_name="SCOUT_PILOT_OBSERVATION_MAX_SECTIONS",
            ),
            observation_max_interactive_elements=_parse_positive_int(
                values.get("SCOUT_PILOT_OBSERVATION_MAX_INTERACTIVE_ELEMENTS", "60"),
                variable_name="SCOUT_PILOT_OBSERVATION_MAX_INTERACTIVE_ELEMENTS",
            ),
            observation_max_form_fields=_parse_positive_int(
                values.get("SCOUT_PILOT_OBSERVATION_MAX_FORM_FIELDS", "25"),
                variable_name="SCOUT_PILOT_OBSERVATION_MAX_FORM_FIELDS",
            ),
            observation_max_dialogs=_parse_positive_int(
                values.get("SCOUT_PILOT_OBSERVATION_MAX_DIALOGS", "5"),
                variable_name="SCOUT_PILOT_OBSERVATION_MAX_DIALOGS",
            ),
            observation_max_section_chars=_parse_positive_int(
                values.get("SCOUT_PILOT_OBSERVATION_MAX_SECTION_CHARS", "700"),
                variable_name="SCOUT_PILOT_OBSERVATION_MAX_SECTION_CHARS",
            ),
            observation_max_total_chars=_parse_positive_int(
                values.get("SCOUT_PILOT_OBSERVATION_MAX_TOTAL_CHARS", "12000"),
                variable_name="SCOUT_PILOT_OBSERVATION_MAX_TOTAL_CHARS",
            ),
            reports_dir=Path(values.get("SCOUT_PILOT_REPORTS_DIR", "reports")),
            llm_provider=values.get("SCOUT_PILOT_LLM_PROVIDER", "openai"),
            llm_model=values.get("SCOUT_PILOT_LLM_MODEL", "gpt-4.1-mini"),
            llm_timeout_seconds=_parse_positive_float(
                values.get("SCOUT_PILOT_LLM_TIMEOUT_SECONDS", "30"),
                variable_name="SCOUT_PILOT_LLM_TIMEOUT_SECONDS",
            ),
            llm_max_output_tokens=_parse_positive_int(
                values.get("SCOUT_PILOT_LLM_MAX_OUTPUT_TOKENS", "1200"),
                variable_name="SCOUT_PILOT_LLM_MAX_OUTPUT_TOKENS",
            ),
            require_confirmation=_parse_bool(
                values.get("SCOUT_PILOT_REQUIRE_CONFIRMATION", "true")
            ),
            max_context_tokens=_parse_positive_int(
                values.get("SCOUT_PILOT_MAX_CONTEXT_TOKENS", "12000"),
                variable_name="SCOUT_PILOT_MAX_CONTEXT_TOKENS",
            ),
            provider_secrets=ProviderSecrets(
                openai_api_key=_empty_to_none(values.get("OPENAI_API_KEY")),
                anthropic_api_key=_empty_to_none(values.get("ANTHROPIC_API_KEY")),
            ),
        )


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _parse_positive_int(value: str, variable_name: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{variable_name} must be positive")
    return parsed


def _parse_positive_float(value: str, variable_name: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"{variable_name} must be positive")
    return parsed


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
