from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Kline
from app.raw_evidence import completed_bars, quality_record

UTC = timezone.utc


def bar(ts: datetime, interval_minutes: int = 1) -> Kline:
    return Kline(
        ts, Decimal('100'), Decimal('101'), Decimal('99'), Decimal('100'),
        Decimal('1'), ts + timedelta(minutes=interval_minutes) - timedelta(microseconds=1),
        Decimal('1000'), 10, Decimal('0.5'), Decimal('500'),
    )


def test_raw_export_excludes_anchor_minute_and_future_bars():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [bar(start + timedelta(minutes=i)) for i in range(6)]
    anchor = start + timedelta(minutes=4)
    selected = completed_bars(bars, anchor, timedelta(minutes=10))
    assert [x.open_time for x in selected] == [start + timedelta(minutes=i) for i in range(4)]
    assert all(x.close_time < anchor for x in selected)


def test_unaligned_anchor_excludes_incomplete_fifteen_minute_bar():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [bar(start + timedelta(minutes=15 * i), 15) for i in range(3)]
    anchor = start + timedelta(minutes=22)
    selected = completed_bars(bars, anchor, timedelta(days=1))
    assert len(selected) == 1
    assert selected[0].open_time == start


def test_quality_report_detects_gap():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [bar(start), bar(start + timedelta(minutes=2))]
    row = quality_record(
        sample_id='s', label='event', event_id='e', source_role='subject',
        source_symbol='XUSDT', interval_minutes=1, target_minutes=3, bars=bars,
    )
    assert row['gap_count'] == 1
    assert row['actual_bar_count'] == 2
