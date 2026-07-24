from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.features import feature_row
from app.models import Kline

UTC=timezone.utc


def test_feature_cutoff_precedes_decision():
    start=datetime(2026,1,1,tzinfo=UTC)
    bars=[]
    for i in range(20):
        ts=start+timedelta(minutes=15*i)
        bars.append(Kline(ts,Decimal('100'),Decimal('101'),Decimal('99'),Decimal('100'),Decimal('1'),ts+timedelta(minutes=15)-timedelta(microseconds=1),Decimal('1000'),10,Decimal('.5'),Decimal('500')))
    anchor=start+timedelta(hours=5)
    row=feature_row('s','event','X',anchor,bars,0)
    assert row['data_cutoff_time'] < row['decision_time']


def test_unaligned_anchor_excludes_incomplete_fifteen_minute_bar():
    start=datetime(2026,1,1,tzinfo=UTC)
    bars=[]
    for i in range(3):
        ts=start+timedelta(minutes=15*i)
        close=Decimal('999') if i==1 else Decimal('100')
        bars.append(Kline(ts,Decimal('100'),Decimal('1000'),Decimal('99'),close,Decimal('1'),ts+timedelta(minutes=15)-timedelta(microseconds=1),Decimal('1000'),10,Decimal('.5'),Decimal('500')))
    anchor=start+timedelta(minutes=22)
    row=feature_row('s','event','X',anchor,bars,0)
    assert row['price']==100.0
    assert row['data_cutoff_time'] < row['decision_time']


def test_reference_context_uses_only_completed_bars():
    start=datetime(2026,1,1,tzinfo=UTC)
    subject=[]
    reference=[]
    for i in range(8):
        ts=start+timedelta(minutes=15*i)
        subject.append(Kline(ts,Decimal('100'),Decimal('102'),Decimal('99'),Decimal(str(100+i)),Decimal('1'),ts+timedelta(minutes=15)-timedelta(microseconds=1),Decimal('1000'),10,Decimal('.5'),Decimal('500')))
        close=Decimal('999') if i==7 else Decimal(str(200+i))
        reference.append(Kline(ts,Decimal('200'),Decimal('1000'),Decimal('199'),close,Decimal('1'),ts+timedelta(minutes=15)-timedelta(microseconds=1),Decimal('2000'),10,Decimal('.5'),Decimal('1000')))
    anchor=start+timedelta(minutes=112)
    row=feature_row('s','event','X',anchor,subject,0,{'BTC':reference})
    assert row['ref_BTC_w60_return'] is not None
    assert row['data_cutoff_time'] < row['decision_time']
