from datetime import datetime, timedelta, timezone

from app.protocol import assign_split, split_boundaries

UTC = timezone.utc


def test_chronological_splits_include_forward_embargo():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=60)
    boundaries = split_boundaries(start, end, horizon_minutes=480)
    assert boundaries.discovery_end == start + timedelta(days=36) - timedelta(hours=8)
    assert boundaries.validation_start == start + timedelta(days=36)
    assert boundaries.validation_end == start + timedelta(days=48) - timedelta(hours=8)
    assert boundaries.sealed_start == start + timedelta(days=48)
    assert assign_split(boundaries.discovery_end - timedelta(minutes=15), boundaries) == 'discovery'
    assert assign_split(boundaries.discovery_end, boundaries) == 'embargo'
    assert assign_split(boundaries.validation_start, boundaries) == 'validation'
    assert assign_split(boundaries.sealed_start, boundaries) == 'sealed_test'
