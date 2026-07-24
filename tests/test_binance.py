from app.binance import _to_datetime


def test_timestamp_ms_and_us():
    assert _to_datetime(1735689600000).year==2025
    assert _to_datetime(1735689600000000).year==2025
