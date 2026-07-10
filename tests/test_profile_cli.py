import subprocess
from pathlib import Path

import pytest

from scout_pilot.cli.profiles import inspect_browser_profile, resolve_profile_path
from scout_pilot.config import AppConfig


def test_default_profile_uses_configured_path():
    config = AppConfig(browser_profile_dir=Path("custom/profile"))

    assert resolve_profile_path(config, "default") == Path("custom/profile")
    assert resolve_profile_path(config, "") == Path("custom/profile")


def test_named_profile_stays_inside_browser_profiles():
    config = AppConfig()

    assert resolve_profile_path(config, "interview") == Path(".browser-profiles/interview")
    assert resolve_profile_path(config, "demo_1") == Path(".browser-profiles/demo_1")


@pytest.mark.parametrize("profile", ["../secret", "bad/name", "bad name", "name\\bad", ""])
def test_invalid_named_profile_is_rejected(profile):
    config = AppConfig()
    if not profile:
        assert resolve_profile_path(config, profile) == config.browser_profile_dir
        return

    with pytest.raises(ValueError):
        resolve_profile_path(config, profile)


def test_profile_git_ignore_detection_uses_repository_rules(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text(".browser-profiles/\n", encoding="utf-8")
    profile_path = tmp_path / ".browser-profiles/default"
    config = AppConfig(browser_profile_dir=Path(".browser-profiles/default"))

    missing_info = inspect_browser_profile(config, cwd=tmp_path)
    profile_path.mkdir(parents=True)
    existing_info = inspect_browser_profile(config, cwd=tmp_path)

    assert missing_info.path == Path(".browser-profiles/default")
    assert missing_info.exists is False
    assert missing_info.git_ignored is True
    assert existing_info.exists is True
    assert existing_info.git_ignored is True


def test_profile_git_ignore_detection_reports_unignored_path(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    config = AppConfig(browser_profile_dir=Path("profiles/default"))

    info = inspect_browser_profile(config, cwd=tmp_path)

    assert info.git_ignored is False
