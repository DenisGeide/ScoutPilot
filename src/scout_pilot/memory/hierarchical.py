"""In-memory hierarchical memory implementation."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from scout_pilot.memory.privacy import MemoryPrivacyFilter
from scout_pilot.memory.summarizer import (
    DeterministicMemorySummarizer,
    MemorySummarizer,
)
from scout_pilot.memory.types import MemorySettings, MemorySnapshot, MemoryUpdateResult
from scout_pilot.models import MemoryLayer, MemoryRecord, MemoryRecordKind


class _BoundedLayerStore:
    """Bounded in-memory storage for one memory layer."""

    def __init__(
        self,
        layer: MemoryLayer,
        max_records: int,
        *,
        preserve_high_importance: bool = False,
    ) -> None:
        self.layer = layer
        self._max_records = max_records
        self._preserve_high_importance = preserve_high_importance
        self._records: list[MemoryRecord] = []

    def update(self, record: MemoryRecord) -> MemoryUpdateResult:
        record = replace(record, layer=self.layer)
        index = self._find_index(record.scope, record.key)
        if index is not None:
            self._records[index] = record
            return MemoryUpdateResult(accepted=True, record=record)
        self._records.append(record)
        self._trim()
        return MemoryUpdateResult(accepted=True, record=record)

    def recall(
        self,
        scope: str | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryRecord, ...]:
        records = [
            record
            for record in self._records
            if scope is None or record.scope == scope
        ]
        if limit is not None:
            records = records[-limit:]
        return tuple(records)

    def clear(self, scope: str | None = None) -> None:
        if scope is None:
            self._records.clear()
            return
        self._records = [record for record in self._records if record.scope != scope]

    def _find_index(self, scope: str, key: str) -> int | None:
        for index, record in enumerate(self._records):
            if record.scope == scope and record.key == key:
                return index
        return None

    def _trim(self) -> None:
        while len(self._records) > self._max_records:
            if self._preserve_high_importance:
                lowest = min(
                    enumerate(self._records),
                    key=lambda item: (item[1].importance, item[0]),
                )[0]
                del self._records[lowest]
            else:
                del self._records[0]


class _WorkingMemoryStore(_BoundedLayerStore):
    """Current-cycle memory with repeated observation compression."""

    def __init__(self, max_records: int) -> None:
        super().__init__(MemoryLayer.WORKING, max_records)

    def update(self, record: MemoryRecord) -> MemoryUpdateResult:
        record = replace(record, layer=MemoryLayer.WORKING)
        duplicate_index = self._find_repeated_observation(record)
        if duplicate_index is not None:
            existing = self._records[duplicate_index]
            repeat_count = int(existing.value.get("repeat_count", 1)) + 1
            compressed = replace(
                existing,
                value={
                    **existing.value,
                    "repeat_count": repeat_count,
                },
            )
            self._records[duplicate_index] = compressed
            return MemoryUpdateResult(
                accepted=True,
                record=compressed,
                compressed=True,
            )
        return super().update(record)

    def _find_repeated_observation(self, record: MemoryRecord) -> int | None:
        if record.kind is not MemoryRecordKind.OBSERVATION:
            return None
        signature = _observation_signature(record)
        if signature is None:
            return None
        for index, existing in enumerate(self._records):
            if (
                existing.scope == record.scope
                and existing.kind is MemoryRecordKind.OBSERVATION
                and _observation_signature(existing) == signature
            ):
                return index
        return None


class _EpisodicMemoryStore(_BoundedLayerStore):
    """Episodic memory that compresses old events into summaries."""

    def __init__(
        self,
        max_records: int,
        summarizer: MemorySummarizer,
    ) -> None:
        super().__init__(MemoryLayer.EPISODIC, max_records)
        self._summarizer = summarizer
        self._summary_index = 0

    def _trim(self) -> None:
        if len(self._records) <= self._max_records:
            return
        overflow = len(self._records) - self._max_records + 1
        candidates = self._records[: max(overflow, 2)]
        summary_text = self._summarizer.summarize(candidates)
        if summary_text:
            self._summary_index += 1
            summary_record = MemoryRecord(
                key=f"episodic_summary_{self._summary_index}",
                value={
                    "summary": summary_text,
                    "compressed_event_count": len(candidates),
                },
                scope=candidates[0].scope,
                layer=MemoryLayer.EPISODIC,
                kind=MemoryRecordKind.SUMMARY,
                importance=max(record.importance for record in candidates),
                source="episodic_memory",
            )
            self._records = [summary_record, *self._records[len(candidates) :]]
        else:
            self._records = self._records[len(candidates) :]
        while len(self._records) > self._max_records:
            del self._records[1 if len(self._records) > 1 else 0]


class HierarchicalMemory:
    """Privacy-aware hierarchical memory for runtime and context consumers."""

    def __init__(
        self,
        settings: MemorySettings | None = None,
        privacy_filter: MemoryPrivacyFilter | None = None,
        summarizer: MemorySummarizer | None = None,
    ) -> None:
        self._settings = settings or MemorySettings()
        self._privacy_filter = privacy_filter or MemoryPrivacyFilter(self._settings)
        self._summarizer = summarizer or DeterministicMemorySummarizer(self._settings)
        self._working = _WorkingMemoryStore(self._settings.max_working_records)
        self._task = _BoundedLayerStore(
            MemoryLayer.TASK,
            self._settings.max_task_records,
            preserve_high_importance=True,
        )
        self._episodic = _EpisodicMemoryStore(
            self._settings.max_episodic_records,
            self._summarizer,
        )

    async def remember(self, record: MemoryRecord) -> None:
        """Persist a memory record after privacy filtering."""

        await self.update(record)

    async def update(self, record: MemoryRecord) -> MemoryUpdateResult:
        """Store a record in the correct memory layer."""

        privacy = self._privacy_filter.sanitize(record)
        if not privacy.accepted or privacy.record is None:
            return MemoryUpdateResult(
                accepted=False,
                reason=privacy.reason,
                redacted=privacy.redacted,
            )
        result = self._layer_store(privacy.record.layer).update(privacy.record)
        return MemoryUpdateResult(
            accepted=result.accepted,
            record=result.record,
            reason=result.reason,
            redacted=privacy.redacted,
            compressed=result.compressed,
        )

    async def recall(self, scope: str) -> Sequence[MemoryRecord]:
        """Recall all memory records for a scope."""

        return (
            *self._task.recall(scope),
            *self._episodic.recall(scope),
            *self._working.recall(scope),
        )

    def recall_layer(
        self,
        layer: MemoryLayer,
        scope: str | None = None,
        limit: int | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Recall records from one memory layer."""

        return self._layer_store(layer).recall(scope=scope, limit=limit)

    def snapshot(self, scope: str) -> MemorySnapshot:
        """Return a bounded snapshot for runtime or context budgeting."""

        return MemorySnapshot(
            working=self._working.recall(scope),
            task=self._task.recall(scope),
            episodic=self._episodic.recall(scope),
            summaries=self.context_summaries(scope),
        )

    def context_summaries(
        self,
        scope: str,
        max_items: int | None = None,
    ) -> tuple[str, ...]:
        """Return bounded provider-neutral memory summaries."""

        limit = max_items or self._settings.max_context_summaries
        task_records = sorted(
            self._task.recall(scope),
            key=lambda record: (-record.importance, record.key),
        )
        episodic_records = self._episodic.recall(scope)
        working_records = self._working.recall(scope)
        summaries = [
            summary
            for summary in (
                *(_summary_for_record(record) for record in task_records),
                *(_summary_for_record(record) for record in episodic_records),
                *(_summary_for_record(record) for record in working_records),
            )
            if summary
        ]
        return tuple(_dedupe_preserve_order(summaries)[:limit])

    def clear_working(self, scope: str | None = None) -> None:
        """Clear current-cycle working memory."""

        self._working.clear(scope)

    async def remember_user_goal(self, scope: str, goal: str) -> MemoryUpdateResult:
        """Preserve the original user goal as a critical task fact."""

        return await self.update(
            MemoryRecord(
                key="user_goal",
                value={"goal": goal},
                scope=scope,
                layer=MemoryLayer.TASK,
                kind=MemoryRecordKind.USER_GOAL,
                importance=100,
                source="user",
            )
        )

    async def remember_constraint(
        self,
        scope: str,
        key: str,
        constraint: str,
    ) -> MemoryUpdateResult:
        """Preserve a selected user or security constraint."""

        return await self.update(
            MemoryRecord(
                key=key,
                value={"constraint": constraint},
                scope=scope,
                layer=MemoryLayer.TASK,
                kind=MemoryRecordKind.CONSTRAINT,
                importance=80,
            )
        )

    async def remember_event(
        self,
        scope: str,
        key: str,
        event: str,
        *,
        importance: int = 10,
    ) -> MemoryUpdateResult:
        """Store a compact episodic event summary."""

        return await self.update(
            MemoryRecord(
                key=key,
                value={"event": event},
                scope=scope,
                layer=MemoryLayer.EPISODIC,
                kind=MemoryRecordKind.EVENT,
                importance=importance,
            )
        )

    def _layer_store(self, layer: MemoryLayer) -> _BoundedLayerStore:
        resolved_layer = MemoryLayer(layer)
        if resolved_layer is MemoryLayer.WORKING:
            return self._working
        if resolved_layer is MemoryLayer.TASK:
            return self._task
        return self._episodic


def _observation_signature(record: MemoryRecord) -> tuple[str, str] | None:
    summary = record.value.get("summary")
    url = record.value.get("url")
    if not isinstance(summary, str):
        return None
    return (str(url or ""), summary.casefold().strip())


def _summary_for_record(record: MemoryRecord) -> str:
    summary = _record_text(record)
    if not summary:
        return ""
    return f"{record.layer.value}.{record.kind.value}: {summary}"


def _record_text(record: MemoryRecord) -> str:
    for key in ("summary", "goal", "constraint", "choice", "warning", "event", "text"):
        value = record.value.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if record.kind is MemoryRecordKind.OBSERVATION:
        title = record.value.get("title")
        url = record.value.get("url")
        summary = record.value.get("summary")
        parts = [str(part) for part in (title, url, summary) if part]
        return " | ".join(parts)
    return ""


def _dedupe_preserve_order(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = value.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(value)
    return output
