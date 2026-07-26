from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class SplitBoundaries:
    discovery_start: datetime
    discovery_end: datetime
    validation_start: datetime
    validation_end: datetime
    sealed_start: datetime
    sealed_end: datetime
    embargo_minutes: int


def _align_down(value: datetime, cadence_minutes: int) -> datetime:
    minute = (value.minute // cadence_minutes) * cadence_minutes
    return value.replace(minute=minute, second=0, microsecond=0)


def split_boundaries(
    start: datetime,
    end: datetime,
    *,
    cadence_minutes: int = 15,
    horizon_minutes: int = 480,
) -> SplitBoundaries:
    """Chronological 60/20/20 boundaries with a forward-label embargo.

    Discovery and validation stop one outcome horizon before the next split.
    This prevents any labelled forward window from crossing a split boundary.
    """
    if start >= end:
        raise ValueError('start must be before end')
    total_seconds = (end - start).total_seconds()
    raw_discovery_boundary = start + timedelta(seconds=total_seconds * 0.60)
    raw_validation_boundary = start + timedelta(seconds=total_seconds * 0.80)
    discovery_boundary = _align_down(raw_discovery_boundary, cadence_minutes)
    validation_boundary = _align_down(raw_validation_boundary, cadence_minutes)
    embargo = timedelta(minutes=horizon_minutes)
    discovery_end = discovery_boundary - embargo
    validation_end = validation_boundary - embargo
    if discovery_end <= start or validation_end <= discovery_boundary or end <= validation_boundary:
        raise ValueError('scan window is too short for chronological splits and embargoes')
    return SplitBoundaries(
        discovery_start=start,
        discovery_end=discovery_end,
        validation_start=discovery_boundary,
        validation_end=validation_end,
        sealed_start=validation_boundary,
        sealed_end=end,
        embargo_minutes=horizon_minutes,
    )


def assign_split(decision_time: datetime, boundaries: SplitBoundaries) -> str:
    if boundaries.discovery_start <= decision_time < boundaries.discovery_end:
        return 'discovery'
    if boundaries.validation_start <= decision_time < boundaries.validation_end:
        return 'validation'
    if boundaries.sealed_start <= decision_time < boundaries.sealed_end:
        return 'sealed_test'
    return 'embargo'
