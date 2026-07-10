import asyncio

from scout_pilot.memory import (
    HierarchicalMemory,
    MemorySettings,
    memory_record_from_dict,
    memory_record_to_dict,
)
from scout_pilot.models import MemoryLayer, MemoryRecord, MemoryRecordKind


def test_working_memory_is_bounded_and_compresses_repeated_observations():
    memory = HierarchicalMemory(MemorySettings(max_working_records=2))

    first = asyncio.run(
        memory.update(
            MemoryRecord(
                key="observation_1",
                value={
                    "url": "https://example.test",
                    "summary": "Same compact page.",
                },
                scope="task-1",
                layer=MemoryLayer.WORKING,
                kind=MemoryRecordKind.OBSERVATION,
            )
        )
    )
    second = asyncio.run(
        memory.update(
            MemoryRecord(
                key="observation_2",
                value={
                    "url": "https://example.test",
                    "summary": "Same compact page.",
                },
                scope="task-1",
                layer=MemoryLayer.WORKING,
                kind=MemoryRecordKind.OBSERVATION,
            )
        )
    )
    asyncio.run(
        memory.update(
            MemoryRecord(
                key="current_focus",
                value={"text": "Search field focused."},
                scope="task-1",
                layer=MemoryLayer.WORKING,
            )
        )
    )

    records = memory.recall_layer(MemoryLayer.WORKING, scope="task-1")

    assert first.accepted is True
    assert second.compressed is True
    assert len(records) == 2
    assert records[0].value["repeat_count"] == 2
    assert records[1].key == "current_focus"


def test_task_memory_preserves_critical_facts_when_bounded():
    memory = HierarchicalMemory(MemorySettings(max_task_records=2))

    asyncio.run(memory.remember_user_goal("task-1", "Find a suitable page"))
    asyncio.run(
        memory.update(
            MemoryRecord(
                key="low_fact_1",
                value={"text": "Transient detail one."},
                scope="task-1",
                layer=MemoryLayer.TASK,
                importance=1,
            )
        )
    )
    asyncio.run(
        memory.update(
            MemoryRecord(
                key="low_fact_2",
                value={"text": "Transient detail two."},
                scope="task-1",
                layer=MemoryLayer.TASK,
                importance=1,
            )
        )
    )

    keys = {record.key for record in memory.recall_layer(MemoryLayer.TASK, scope="task-1")}
    summaries = memory.context_summaries("task-1")

    assert "user_goal" in keys
    assert len(keys) == 2
    assert any("Find a suitable page" in summary for summary in summaries)


def test_episodic_memory_summarizes_old_events():
    memory = HierarchicalMemory(MemorySettings(max_episodic_records=3))

    for index in range(5):
        asyncio.run(
            memory.remember_event(
                "task-1",
                key=f"event_{index}",
                event=f"Completed event {index}.",
            )
        )

    records = memory.recall_layer(MemoryLayer.EPISODIC, scope="task-1")

    assert len(records) <= 3
    assert records[0].kind is MemoryRecordKind.SUMMARY
    assert records[0].value["compressed_event_count"] >= 2
    assert "Completed event" in records[0].value["summary"]


def test_privacy_filter_blocks_private_records_and_redacts_sensitive_fields():
    memory = HierarchicalMemory()

    rejected = asyncio.run(
        memory.update(
            MemoryRecord(
                key="private",
                value={"text": "Private content."},
                scope="task-1",
                contains_private_data=True,
                layer=MemoryLayer.TASK,
            )
        )
    )
    redacted = asyncio.run(
        memory.update(
            MemoryRecord(
                key="form_summary",
                value={
                    "text": "Form was filled.",
                    "password": "fake-password-value",
                    "raw_html": "<html><body><input value='fake-password-value'></body></html>",
                    "screenshot_path": "diagnostics/screenshot-private.png",
                },
                scope="task-1",
                layer=MemoryLayer.TASK,
            )
        )
    )

    records = memory.recall_layer(MemoryLayer.TASK, scope="task-1")
    serialized = str([memory_record_to_dict(record) for record in records])

    assert rejected.accepted is False
    assert redacted.accepted is True
    assert redacted.redacted is True
    assert "fake-password-value" not in serialized
    assert "<html" not in serialized
    assert "screenshot-private" not in serialized
    assert records[0].value == {"text": "Form was filled."}


def test_memory_snapshot_and_safe_serialization_are_bounded():
    memory = HierarchicalMemory(MemorySettings(max_context_summaries=2))

    asyncio.run(memory.remember_user_goal("task-1", "Find documentation"))
    asyncio.run(memory.remember_constraint("task-1", "language", "Use Russian output."))
    asyncio.run(memory.remember_event("task-1", "event_1", "Observed the start page."))

    snapshot = memory.snapshot("task-1")
    serialized = memory_record_to_dict(snapshot.task[0])
    restored = memory_record_from_dict(serialized)

    assert len(snapshot.summaries) == 2
    assert snapshot.task
    assert snapshot.episodic
    assert restored.key == snapshot.task[0].key
    assert restored.layer is snapshot.task[0].layer
    assert restored.contains_private_data is False
