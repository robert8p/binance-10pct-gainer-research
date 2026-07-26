from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

from app.exporter import GridEvidencePackageBuilder, _create_subject_db, _bar_tuple
from app.config import Settings
from app.models import Kline

UTC = timezone.utc


def bar(ts, minutes):
    return Kline(
        ts, Decimal('100'), Decimal('101'), Decimal('99'), Decimal('100'), Decimal('1'),
        ts + timedelta(minutes=minutes) - timedelta(microseconds=1), Decimal('1000'), 10,
        Decimal('0.5'), Decimal('500'),
    )


def test_subject_view_is_strictly_pre_decision(tmp_path):
    db = tmp_path / 'subject.sqlite'
    conn = _create_subject_db(db)
    decision = datetime(2026, 1, 2, tzinfo=UTC)
    conn.execute('insert into candidates values (?,?,?,?,?,?)', ('c1','XUSDT','X','USDT',decision.isoformat(),'discovery'))
    conn.execute('insert into outcomes values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
        'c1','100','1000',10,1,'110',0,None,None,'109','9','0',0,0,1,0,'test'
    ))
    conn.execute('insert into candidate_windows values (?,?,?,?)', (
        'c1',1,(decision-timedelta(minutes=2)).isoformat(),decision.isoformat()
    ))
    before = bar(decision-timedelta(minutes=1),1)
    at_decision = bar(decision,1)
    conn.executemany('insert into bars values (?,?,?,?,?,?,?,?,?,?,?,?,?)', [
        _bar_tuple('XUSDT',1,before), _bar_tuple('XUSDT',1,at_decision)
    ])
    conn.commit()
    rows = conn.execute('select open_time,close_time from candidate_bars').fetchall()
    assert len(rows) == 1
    assert rows[0][0] == before.open_time.isoformat()
    assert rows[0][1] < decision.isoformat()
    conn.close()


def test_builder_uses_unique_attempt_prefix(tmp_path):
    builder = GridEvidencePackageBuilder(Settings(temp_data_dir=tmp_path), 'job-1', {'x': 1})
    assert builder.storage_prefix.startswith('grid-evidence/job-1/attempt_')
    assert builder.metadata['storage_prefix'] == builder.storage_prefix
