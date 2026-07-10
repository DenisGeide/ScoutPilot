"""Safe serialization helpers for hierarchical memory."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from uuid import uuid4

from scout_pilot.models import MemoryLayer, MemoryRecord, MemoryRecordKind


def memory_record_to_dict(record: MemoryRecord) -> Mapping[str, Any]:
    """Serialize one sanitized memory record."""

    return {
        "record_id": record.record_id,
        "key": record.key,
        "value": dict(record.value),
        "scope": record.scope,
        "layer": record.layer.value,
        "kind": record.kind.value,
        "importance": record.importance,
        "source": record.source,
    }


def memory_record_from_dict(data: Mapping[str, Any]) -> MemoryRecord:
    """Deserialize one sanitized memory record."""

    return MemoryRecord(
        key=str(data["key"]),
        value=_mapping_value(data.get("value")),
        scope=str(data["scope"]),
        contains_private_data=False,
        layer=MemoryLayer(str(data.get("layer", MemoryLayer.TASK.value))),
        kind=MemoryRecordKind(str(data.get("kind", MemoryRecordKind.FACT.value))),
        importance=int(data.get("importance", 1)),
        source=str(data["source"]) if data.get("source") is not None else None,
        record_id=str(data.get("record_id") or uuid4().hex),
    )


def memory_records_to_dicts(records: Sequence[MemoryRecord]) -> tuple[Mapping[str, Any], ...]:
    """Serialize a sequence of sanitized memory records."""

    return tuple(memory_record_to_dict(record) for record in records)


def _mapping_value(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}
