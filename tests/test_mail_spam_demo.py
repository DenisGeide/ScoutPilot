import asyncio
import json
from pathlib import Path

from scout_pilot.config import AppConfig
from scout_pilot.demo import MailSpamDemoSettings, run_local_mail_spam_demo


def test_mail_spam_demo_reads_ten_messages_and_pauses_before_destructive_action(tmp_path):
    progress: list[str] = []

    result = asyncio.run(
        run_local_mail_spam_demo(
            AppConfig.load(),
            MailSpamDemoSettings(
                site_dir=tmp_path / "site",
                profile_dir=tmp_path / "profile",
                report_path=tmp_path / "mail-report.json",
                replay_path=tmp_path / "mail-replay.json",
                headless=True,
                slow_mo_ms=0,
            ),
            progress=progress.append,
        )
    )

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    replay = json.loads(result.replay_path.read_text(encoding="utf-8"))
    serialized = json.dumps(report, ensure_ascii=False).casefold()

    assert result.success is True
    assert result.messages_read == 10
    assert result.spam_candidates >= 4
    assert result.security_pause_count >= 1
    assert report["artifact_kind"] == "demo_report"
    assert report["demo_name"] == "synthetic_mail_spam"
    assert replay["artifact_kind"] == "demo_replay"
    assert report["stopped_before_side_effects"] is True
    assert len(report["discovered_urls"]) == 10
    assert len(report["pages_read"]) == 10
    assert len(report["notes"]) == 10
    assert report["summary"]["discovered_url_count"] == 10
    assert report["summary"]["pages_read_count"] == 10
    assert report["summary"]["security_pause_count"] >= 1
    assert any(note["classification"] == "likely_spam" for note in report["notes"])
    assert any(pause["risk"] == "destructive" for pause in report["security_pauses"])
    assert any("Готовлю локальный синтетический почтовый сайт." in item for item in progress)
    assert any("Читаю письмо 10/10." in item for item in progress)
    assert any("Остановился перед удалением" in item for item in progress)
    assert "<html" not in serialized
    assert "<button" not in serialized
    assert "token=" not in serialized
    assert "cookie" not in serialized
    assert "yandex" not in serialized
    assert "gmail" not in serialized


def test_mail_spam_demo_source_does_not_use_real_mail_or_site_specific_selectors():
    source_root = Path(__file__).resolve().parents[1] / "src" / "scout_pilot"
    source = (source_root / "demo" / "mail_spam.py").read_text(
        encoding="utf-8"
    ).casefold()
    forbidden = (
        "yandex",
        "gmail",
        "mail.ru",
        "imap",
        "smtp",
        "data-qa",
        "queryselector",
        "locator(",
        "xpath",
    )

    for term in forbidden:
        assert term not in source
