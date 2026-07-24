from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MethodType

from app.binance import BinanceClient
from app.config import Settings

UTC=timezone.utc


def test_exact_crossing_excludes_trades_before_threshold(tmp_path):
    client=BinanceClient(Settings(temp_data_dir=tmp_path))
    minute=datetime(2026,1,1,12,0,tzinfo=UTC)
    trades=[
        (minute+timedelta(seconds=2),Decimal('109'),Decimal('10'),'hash'),
        (minute+timedelta(seconds=5),Decimal('110'),Decimal('2'),'hash'),
        (minute+timedelta(seconds=6),Decimal('111'),Decimal('3'),'hash'),
        (minute+timedelta(minutes=6),Decimal('120'),Decimal('100'),'hash'),
    ]
    def fake_iter(self,symbol,day):
        yield from trades
    client._iter_archived_aggtrades=MethodType(fake_iter,client)
    exact,notional,count,digests=client.archived_saleability('XUSDT',minute,Decimal('110'),300)
    assert exact==minute+timedelta(seconds=5)
    assert notional==Decimal('553')
    assert count==2
    assert digests==['hash']
