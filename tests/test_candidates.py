from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Kline
from app.scanner import candidate_groups

UTC=timezone.utc


def k(ts,o,h,l,c):
    return Kline(ts,Decimal(str(o)),Decimal(str(h)),Decimal(str(l)),Decimal(str(c)),Decimal('1'),ts+timedelta(minutes=15)-timedelta(microseconds=1),Decimal('1000'),10,Decimal('.5'),Decimal('500'))


def test_prescreen_catches_fast_move_inside_one_fifteen_minute_bar():
    start=datetime(2026,1,1,tzinfo=UTC)
    bars=[k(start+timedelta(minutes=15*i),100,101,100,100) for i in range(32)]
    bars.append(k(start+timedelta(minutes=15*32),100,111,100,110))
    assert candidate_groups(bars,Decimal('10'),480)
