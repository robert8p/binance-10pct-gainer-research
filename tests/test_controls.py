from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.controls import select_controls
from app.models import Kline

UTC = timezone.utc


def bar(ts: datetime) -> Kline:
    return Kline(
        ts, Decimal('100'), Decimal('100.1'), Decimal('99.9'), Decimal('100'),
        Decimal('1'), ts + timedelta(minutes=15) - timedelta(microseconds=1),
        Decimal('1000'), 10, Decimal('0.5'), Decimal('500'),
    )


def test_controls_use_calendar_matching_not_predictor_metrics():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [bar(start + timedelta(minutes=15 * i)) for i in range(80 * 24 * 4)]
    event_anchor = start + timedelta(days=40, hours=12)
    controls = select_controls(
        bars, event_anchor, [event_anchor], controls_per_event=3,
        threshold_pct=10, window_minutes=480,
    )
    assert len(controls) == 3
    assert all(c['pseudo_baseline_time'].hour == event_anchor.hour for c in controls)
    assert all('ret_24h' not in c and 'rv_24h' not in c and 'qv_24h' not in c for c in controls)
    assert controls[0]['same_weekday'] is True
