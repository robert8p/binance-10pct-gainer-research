from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Kline
from app.scanner import detect_events_from_minutes

UTC=timezone.utc


def bar(ts, o, h, l, c, qv=1000):
    return Kline(ts,Decimal(str(o)),Decimal(str(h)),Decimal(str(l)),Decimal(str(c)),Decimal('1'),ts+timedelta(seconds=59),Decimal(str(qv)),10,Decimal('0.5'),Decimal(str(qv/2)))


def test_detects_first_10pct_crossing_and_cooldown():
    start=datetime(2026,1,1,tzinfo=UTC)
    bars=[bar(start+timedelta(minutes=i),100,101,100,100) for i in range(20)]
    bars.append(bar(start+timedelta(minutes=20),109,111,108,110))
    bars.append(bar(start+timedelta(minutes=21),110,112,109,111))
    events=detect_events_from_minutes('XUSDT','X','USDT',bars,Decimal('10'),480,480)
    assert len(events)==1
    assert events[0].crossing_time==start+timedelta(minutes=20)
    assert events[0].baseline_time==start


def test_does_not_use_current_bar_low_as_baseline():
    start=datetime(2026,1,1,tzinfo=UTC)
    bars=[bar(start,100,101,100,100),bar(start+timedelta(minutes=1),100,110,90,100)]
    events=detect_events_from_minutes('XUSDT','X','USDT',bars,Decimal('10'),480,480)
    assert len(events)==1
    assert events[0].baseline_price==Decimal('100')
