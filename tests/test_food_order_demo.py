import asyncio
import json
from pathlib import Path

from scout_pilot.config import AppConfig
from scout_pilot.demo import FoodOrderDemoSettings, run_local_food_order_demo


def test_food_order_demo_reaches_checkout_and_pauses_before_payment(tmp_path):
    progress: list[str] = []

    result = asyncio.run(
        run_local_food_order_demo(
            AppConfig.load(),
            FoodOrderDemoSettings(
                site_dir=tmp_path / "site",
                profile_dir=tmp_path / "profile",
                report_path=tmp_path / "food-report.json",
                replay_path=tmp_path / "food-replay.json",
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
    assert result.selected_items == ("BBQ Burger", "French Fries")
    assert result.checkout_reached is True
    assert result.security_pause_count >= 1
    assert report["artifact_kind"] == "demo_report"
    assert report["demo_name"] == "synthetic_food_order"
    assert replay["artifact_kind"] == "demo_replay"
    assert report["stopped_before_side_effects"] is True
    assert report["summary"]["pages_read_count"] >= 3
    assert report["summary"]["security_pause_count"] >= 1
    assert any(note["item_name"] == "BBQ Burger" for note in report["notes"])
    assert any(note["item_name"] == "French Fries" for note in report["notes"])
    assert any(
        pause["risk"] == "external_side_effect"
        and pause["phase"] == "probe_payment_safety"
        for pause in report["security_pauses"]
    )
    assert any(event["kind"] == "decision" for event in report["events"])
    assert any(event["kind"] == "selected_tool" for event in report["events"])
    assert any("Добавляю в корзину: BBQ Burger." in item for item in progress)
    assert any("Остановился перед оплатой" in item for item in progress)
    assert "bbq bacon burger" in serialized
    assert "bbq burger combo" in serialized
    assert "<html" not in serialized
    assert "<button" not in serialized
    assert "token=" not in serialized
    assert "cookie" not in serialized
    assert "address" not in serialized
    assert "card" not in serialized


def test_food_order_demo_source_has_no_real_delivery_or_workflow_selectors():
    source_root = Path(__file__).resolve().parents[1] / "src" / "scout_pilot"
    source = (source_root / "demo" / "food_order.py").read_text(
        encoding="utf-8"
    ).casefold()
    forbidden = (
        "ubereats",
        "doordash",
        "yandex",
        "deliveryclub",
        "samokat",
        "data-qa",
        "queryselector",
        "locator(",
        "xpath",
    )

    for term in forbidden:
        assert term not in source
