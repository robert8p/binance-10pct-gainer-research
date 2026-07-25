from app.exporter import _event_splits


def test_event_group_splits_keep_controls_with_events():
    rows=[]
    for i in range(10):
        rows.append({'sample_id':f'e{i}','label':'event','event_id':str(i),'anchor_time':f'2026-01-{i+1:02d}'})
        rows.append({'sample_id':f'c{i}','label':'control','event_id':str(i),'anchor_time':f'2025-12-{i+1:02d}'})
    splits=_event_splits(rows)
    assert len(splits['discovery'])==6
    assert len(splits['validation'])==2
    assert len(splits['sealed_test'])==2
    assert set.union(*splits.values())=={str(i) for i in range(10)}

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

from app.exporter import _Shard
from app.models import Kline

UTC = timezone.utc


def _bar(ts, minutes):
    return Kline(
        ts, Decimal('100'), Decimal('101'), Decimal('99'), Decimal('100'),
        Decimal('1'), ts + timedelta(minutes=minutes) - timedelta(microseconds=1),
        Decimal('1000'), 10, Decimal('0.5'), Decimal('500'),
    )


def test_sqlite_evidence_is_normalised_and_point_in_time(tmp_path):
    shard = _Shard(tmp_path / 'part', 'discovery', 1, ['e1'])
    anchor = datetime(2026, 1, 2, tzinfo=UTC)
    shared = _bar(anchor - timedelta(minutes=2), 1)
    sample = {
        'sample_id': 'event:e1', 'event_id': 'e1', 'control_id': None,
        'symbol': 'XUSDT', 'base_asset': 'X', 'quote_asset': 'USDT',
        'anchor_time': anchor.isoformat(), 'sample_kind': 'event',
        'control_rank': None, 'control_selection_basis': None,
    }
    outcome = {
        'sample_id': 'event:e1', 'event_id': 'e1', 'sample_kind': 'event',
        'did_10pct_event_occur': True, 'crossing_time': anchor.isoformat(),
        'minutes_to_cross': 5, 'gain_pct': 10.0, 'exit_quote_notional': 1000.0,
        'saleable': True, 'saleability_source': 'test',
    }
    shard.add_sample(
        sample=sample, outcome=outcome,
        subject_15m=[_bar(anchor - timedelta(minutes=15), 15)],
        subject_1m=[shared], market_15m={'BTCUSDT': []}, market_1m={'BTCUSDT': [shared]},
    )
    counts = shard.finalise_files({'protocol_version': 'test'})
    assert counts['sample_count'] == 1
    # The same timestamp is retained separately by symbol, not duplicated by sample role.
    with sqlite3.connect(tmp_path / 'part' / 'raw_evidence.sqlite') as conn:
        assert conn.execute('select count(*) from samples').fetchone()[0] == 1
        assert conn.execute('select count(*) from sample_windows').fetchone()[0] == 4
        assert conn.execute('select max(close_time) < ? from sample_bars', (anchor.isoformat(),)).fetchone()[0] == 1
        assert conn.execute('select count(*) from sample_bars').fetchone()[0] == 3
