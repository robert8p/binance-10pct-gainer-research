from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Iterable

from .models import Kline


def has_threshold_event(
    bars: list[Kline],
    anchor: datetime,
    threshold_pct: float,
    window_minutes: int,
) -> bool:
    """Conservatively reject controls near any coarse threshold crossing."""
    start = anchor - timedelta(minutes=window_minutes)
    end = anchor + timedelta(minutes=window_minutes)
    lookback_start = start - timedelta(minutes=window_minutes)
    subset = [x for x in bars if lookback_start <= x.open_time < end]
    if not subset:
        return True
    threshold = 1 + (threshold_pct - 0.25) / 100
    low_deque: deque[int] = deque()
    for idx, bar in enumerate(subset):
        cutoff = bar.open_time - timedelta(minutes=window_minutes)
        while low_deque and subset[low_deque[0]].open_time < cutoff:
            low_deque.popleft()
        if bar.open_time >= start and low_deque:
            baseline = min(subset[low_deque[0]].low, bar.low)
            if baseline <= 0 or float(bar.high / baseline) >= threshold:
                return True
        while low_deque and subset[low_deque[-1]].low > bar.low:
            low_deque.pop()
        low_deque.append(idx)
    return False


def select_controls(
    bars: list[Kline],
    event_anchor: datetime,
    excluded_times: Iterable[datetime],
    controls_per_event: int = 5,
    threshold_pct: float = 10.0,
    window_minutes: int = 480,
    min_separation_days: int = 2,
) -> list[dict[str, object]]:
    """Select neutral same-symbol, same-time controls without predictor matching.

    The application does not decide that return, volatility, volume or another
    engineered feature is important. Eligible controls are ranked mechanically:
    same UTC 15-minute slot, then same weekday, then nearest calendar date.
    """
    excluded = list(excluded_times)
    candidates: list[dict[str, object]] = []
    slot_minute = (event_anchor.minute // 15) * 15
    start = bars[0].open_time + timedelta(days=10) if bars else event_anchor
    end = bars[-1].close_time - timedelta(minutes=window_minutes) if bars else event_anchor
    cursor = start.replace(hour=event_anchor.hour, minute=slot_minute, second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(days=1)
    while cursor < end:
        distance_days = abs((cursor.date() - event_anchor.date()).days)
        if distance_days < min_separation_days:
            cursor += timedelta(days=1)
            continue
        if any(abs((cursor - ts).total_seconds()) < window_minutes * 60 for ts in excluded):
            cursor += timedelta(days=1)
            continue
        if has_threshold_event(bars, cursor, threshold_pct, window_minutes):
            cursor += timedelta(days=1)
            continue
        weekday_penalty = 0 if cursor.weekday() == event_anchor.weekday() else 1
        candidates.append({
            'pseudo_baseline_time': cursor,
            'match_score': weekday_penalty * 10000 + distance_days,
            'match_basis': 'same_symbol_same_utc_slot; same_weekday_then_nearest_date',
            'same_weekday': weekday_penalty == 0,
            'calendar_distance_days': distance_days,
        })
        cursor += timedelta(days=1)
    candidates.sort(key=lambda x: (
        0 if x['same_weekday'] else 1,
        int(x['calendar_distance_days']),
        x['pseudo_baseline_time'],
    ))
    return candidates[:controls_per_event]
