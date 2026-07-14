import asyncio
import subprocess
from pathlib import Path

from scout_pilot.cli.doctor import (
    DoctorCheck,
    DoctorReport,
    DoctorSettings,
    check_architecture_boundaries,
    check_browser_profile,
    check_env_file,
    check_git_status,
    check_provider_key,
    check_python_version,
    format_doctor_report,
    run_doctor,
)
from scout_pilot.config import AppConfig, ProviderSecrets


def test_python_version_check_blocks_old_python():
    result = check_python_version(version_info=(3, 10, 12))

    assert result.status == "failed"
    assert result.blocker is True
    assert "3.11+" in result.message_ru


def test_env_file_missing_is_warning_not_blocker(tmp_path):
    result = check_env_file(Path(".env"), cwd=tmp_path)

    assert result.status == "warning"
    assert result.blocker is False
    assert ".env" in result.label


def test_requested_provider_key_is_required():
    missing = check_provider_key(AppConfig(provider_secrets=ProviderSecrets()), "openai")
    present = check_provider_key(
        AppConfig(provider_secrets=ProviderSecrets(openai_api_key="unit-test-key")),
        "openai",
    )

    assert missing.status == "failed"
    assert missing.blocker is True
    assert "OPENAI_API_KEY" in missing.message_ru
    assert present.status == "ok"
    assert "unit-test-key" not in present.message_ru


def test_browser_profile_unignored_path_is_blocker(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    config = AppConfig(browser_profile_dir=Path("profiles/default"))

    result = check_browser_profile(config, cwd=tmp_path)

    assert result.status == "failed"
    assert result.blocker is True
    assert "profiles/default" in result.message_ru


def test_git_dirty_tree_is_warning_not_blocker(tmp_path):
    def fake_runner(args, cwd):
        if args[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(args, 0, stdout=str(tmp_path), stderr="")
        if args[:3] == ["git", "status", "--short"]:
            return subprocess.CompletedProcess(args, 0, stdout=" M README.md\n", stderr="")
        raise AssertionError(args)

    result = check_git_status(cwd=tmp_path, command_runner=fake_runner)

    assert result.status == "warning"
    assert result.blocker is False
    assert "README.md" in result.message_ru


def test_architecture_boundary_check_accepts_the_current_repository():
    repository_root = Path(__file__).resolve().parents[1]

    result = check_architecture_boundaries(cwd=repository_root)

    assert result.status == "ok"
    assert result.blocker is False
    assert "Playwright" in result.message_ru
    assert "HH.ru" in result.message_ru


def test_architecture_boundary_check_blocks_playwright_outside_browser(tmp_path):
    runtime_dir = tmp_path / "src" / "scout_pilot" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "bad_import.py").write_text(
        "from playwright.async_api import Page\n",
        encoding="utf-8",
    )

    result = check_architecture_boundaries(cwd=tmp_path)

    assert result.status == "failed"
    assert result.blocker is True
    assert "Playwright" in result.message_ru


def test_run_doctor_uses_fake_browser_smoke_and_reports_blockers(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".gitignore").write_text(".browser-profiles/\nreports/tmp/\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    def fake_browser_smoke(_config, _cwd):
        return DoctorCheck(
            key="chromium_launch",
            label="Chromium",
            status="ok",
            message_ru="fake smoke ok.",
        )

    report = asyncio.run(
        run_doctor(
            DoctorSettings(provider="openai", cwd=tmp_path),
            browser_smoke_runner=fake_browser_smoke,
        )
    )

    assert report.exit_code == 1
    assert any(check.key == "provider_key" and check.blocker for check in report.checks)
    assert any(check.key == "chromium_launch" and check.status == "ok" for check in report.checks)


def test_run_doctor_reads_env_file_relative_to_configured_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=unit-test-key\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".browser-profiles/\nreports/tmp/\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    def fake_browser_smoke(_config, _cwd):
        return DoctorCheck(
            key="chromium_launch",
            label="Chromium",
            status="ok",
            message_ru="fake smoke ok.",
        )

    report = asyncio.run(
        run_doctor(
            DoctorSettings(provider="openai", cwd=tmp_path),
            browser_smoke_runner=fake_browser_smoke,
        )
    )

    provider_check = next(check for check in report.checks if check.key == "provider_key")

    assert provider_check.status == "ok"
    assert report.exit_code == 0


def test_format_doctor_report_is_russian_and_marks_failures():
    report = DoctorReport(
        checks=(
            DoctorCheck("python", "Python", "ok", "3.11 подходит."),
            DoctorCheck("provider_key", "LLM ключ", "failed", "OPENAI_API_KEY не найден.", True),
        )
    )

    lines = format_doctor_report(report)

    assert lines[0].startswith("Проверяю")
    assert any("[ОШИБКА] LLM ключ" in line for line in lines)
    assert any("есть блокеры" in line for line in lines)
