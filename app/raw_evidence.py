from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from .models import Kline

SUBJECT_HISTORY_DAYS = 10
HIGH_RESOLUTION_HOURS = 48
REFERENCE_SYMBOLS = {
    'BTC': 'BTCUSDT',
    'ETH': 'ETHUSDT',
    'BNB': 'BNBUSDT',
}


def completed_bars(
    bars: Iterable[Kline],
    anchor_time: datetime,
    history: timedelta,
) -> list[Kline]:
    """Return only fully completed bars before the sample anchor.

    The anchor minute itself is excluded. This is deliberately conservative:
    an event baseline is a minute-bar low whose precise intra-minute timestamp
    is unknown, so retaining that minute could leak information that was not
    yet available at the decision point.
    """
    start = anchor_time - history
    return [bar for bar in bars if bar.open_time >= start and bar.close_time < anchor_time]


def decimal_text(value: Decimal) -> str:
    """Preserve exchange precision in CSV rather than coercing to float."""
    return format(value, 'f')


def bar_record(
    *,
    sample_id: str,
    label: str,
    event_id: str,
    source_symbol: str,
    bar: Kline,
) -> dict[str, object]:
    return {
        'sample_id': sample_id,
        'label': label,
        'event_id': event_id,
        'source_symbol': source_symbol,
        'open_time': bar.open_time.isoformat(),
        'close_time': bar.close_time.isoformat(),
        'open': decimal_text(bar.open),
        'high': decimal_text(bar.high),
        'low': decimal_text(bar.low),
        'close': decimal_text(bar.close),
        'base_volume': decimal_text(bar.volume),
        'quote_volume': decimal_text(bar.quote_volume),
        'trade_count': bar.trades,
        'taker_buy_base_volume': decimal_text(bar.taker_buy_base),
        'taker_buy_quote_volume': decimal_text(bar.taker_buy_quote),
    }


def quality_record(
    *,
    sample_id: str,
    label: str,
    event_id: str,
    source_role: str,
    source_symbol: str,
    interval_minutes: int,
    target_minutes: int,
    bars: list[Kline],
) -> dict[str, object]:
    expected = max(1, target_minutes // interval_minutes)
    gaps = 0
    duplicates = 0
    non_monotonic = 0
    seen: set[datetime] = set()
    previous: datetime | None = None
    expected_step = timedelta(minutes=interval_minutes)
    for bar in bars:
        if bar.open_time in seen:
            duplicates += 1
        seen.add(bar.open_time)
        if previous is not None:
            delta = bar.open_time - previous
            if delta <= timedelta(0):
                non_monotonic += 1
            elif delta > expected_step:
                gaps += max(1, round(delta / expected_step) - 1)
        previous = bar.open_time
    return {
        'sample_id': sample_id,
        'label': label,
        'event_id': event_id,
        'source_role': source_role,
        'source_symbol': source_symbol,
        'interval_minutes': interval_minutes,
        'target_minutes': target_minutes,
        'target_bar_count': expected,
        'actual_bar_count': len(bars),
        'coverage_ratio': round(len(bars) / expected, 6),
        'first_open_time': bars[0].open_time.isoformat() if bars else None,
        'last_close_time': bars[-1].close_time.isoformat() if bars else None,
        'gap_count': gaps,
        'duplicate_count': duplicates,
        'non_monotonic_count': non_monotonic,
        'complete_enough': len(bars) >= expected * 0.98 and gaps <= max(2, expected // 200),
    }
