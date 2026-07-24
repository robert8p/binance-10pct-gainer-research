from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from .binance import BinanceClient
from .models import DetectedEvent, Kline

UTC = timezone.utc


def candidate_groups(
    bars: list[Kline],
    threshold_pct: Decimal,
    window_minutes: int,
    prescreen_buffer_pct: Decimal = Decimal('0.25'),
) -> list[tuple[datetime, datetime]]:
    """Return compact candidate ranges from 15-minute bars.

    The baseline uses only completed bars before the candidate bar. A small
    prescreen buffer avoids false negatives from coarse-bar approximation.
    """
    if not bars:
        return []
    bars_per_window = max(1, window_minutes // 15)
    threshold = Decimal('1') + (threshold_pct - prescreen_buffer_pct) / Decimal('100')
    low_deque: deque[int] = deque()
    candidate_indexes: list[int] = []

    for idx, bar in enumerate(bars):
        while low_deque and low_deque[0] < idx - bars_per_window:
            low_deque.popleft()
        if low_deque:
            # The current 15-minute low is allowed only in the coarse pre-screen
            # so fast rises that begin and cross within one coarse bar are not
            # missed. One-minute verification still requires the low to occur in
            # a completed minute before the crossing minute.
            baseline = min(bars[low_deque[0]].low, bar.low)
            if baseline > 0 and bar.high >= baseline * threshold:
                candidate_indexes.append(idx)
        while low_deque and bars[low_deque[-1]].low > bar.low:
            low_deque.pop()
        low_deque.append(idx)

    if not candidate_indexes:
        return []
    groups: list[tuple[int, int]] = []
    start = previous = candidate_indexes[0]
    for idx in candidate_indexes[1:]:
        if idx <= previous + 2:
            previous = idx
            continue
        groups.append((start, previous))
        start = previous = idx
    groups.append((start, previous))
    return [
        (bars[max(0, start - bars_per_window)].open_time, bars[min(len(bars) - 1, end + 1)].close_time)
        for start, end in groups
    ]


def detect_events_from_minutes(
    symbol: str,
    base_asset: str,
    quote_asset: str,
    bars: list[Kline],
    threshold_pct: Decimal = Decimal('10'),
    window_minutes: int = 480,
    cooldown_minutes: int = 480,
) -> list[DetectedEvent]:
    if not bars:
        return []
    threshold_multiple = Decimal('1') + threshold_pct / Decimal('100')
    low_deque: deque[int] = deque()
    events: list[DetectedEvent] = []
    cooldown_until: datetime | None = None

    for idx, bar in enumerate(bars):
        cutoff = bar.open_time - timedelta(minutes=window_minutes)
        while low_deque and bars[low_deque[0]].open_time < cutoff:
            low_deque.popleft()
        if low_deque and (cooldown_until is None or bar.open_time >= cooldown_until):
            baseline_idx = low_deque[0]
            baseline_bar = bars[baseline_idx]
            threshold_price = baseline_bar.low * threshold_multiple
            if baseline_bar.low > 0 and bar.high >= threshold_price:
                minutes_to_cross = max(1, int((bar.open_time - baseline_bar.open_time).total_seconds() // 60))
                events.append(DetectedEvent(
                    symbol=symbol,
                    base_asset=base_asset,
                    quote_asset=quote_asset,
                    baseline_time=baseline_bar.open_time,
                    baseline_price=baseline_bar.low,
                    crossing_time=bar.open_time,
                    crossing_bar_open=bar.open,
                    crossing_bar_high=bar.high,
                    threshold_price=threshold_price,
                    gain_pct=(bar.high / baseline_bar.low - Decimal('1')) * Decimal('100'),
                    minutes_to_cross=minutes_to_cross,
                    exit_quote_notional=Decimal('0'),
                    exit_trade_count=0,
                    saleability_source='pending',
                    saleable=False,
                ))
                cooldown_until = bar.open_time + timedelta(minutes=cooldown_minutes)
        while low_deque and bars[low_deque[-1]].low > bar.low:
            low_deque.pop()
        low_deque.append(idx)
    return events


def enrich_saleability(
    client: BinanceClient,
    event: DetectedEvent,
    minute_bars: Iterable[Kline],
    saleability_seconds: int,
    min_exit_notional: Decimal,
) -> DetectedEvent:
    exact_cross = event.crossing_time
    end = exact_cross + timedelta(seconds=saleability_seconds)
    quote_notional = Decimal('0')
    trade_count = 0
    source = 'aggTrades_archive_exact_crossing'
    try:
        exact_cross, quote_notional, trade_count, _digests = client.archived_saleability(
            event.symbol, event.crossing_time, event.threshold_price, saleability_seconds
        )
    except Exception:  # archive may not yet exist for the most recent completed day
        source = 'one_minute_quote_volume_fallback_from_bar_open'
        for bar in minute_bars:
            if event.crossing_time <= bar.open_time < end:
                quote_notional += bar.quote_volume
                trade_count += bar.trades
    minutes_to_cross = max(1, int((exact_cross - event.baseline_time).total_seconds() // 60))
    return DetectedEvent(
        **{**event.__dict__,
           'crossing_time': exact_cross,
           'minutes_to_cross': minutes_to_cross,
           'exit_quote_notional': quote_notional,
           'exit_trade_count': trade_count,
           'saleability_source': source,
           'saleable': quote_notional >= min_exit_notional}
    )
