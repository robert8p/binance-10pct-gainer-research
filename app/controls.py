from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import math
from typing import Iterable

from .features import summary_match_metrics
from .models import Kline


def _valid(metrics: dict[str, float | None]) -> bool:
    return all(metrics.get(key) is not None for key in ('ret_24h', 'rv_24h', 'qv_24h', 'ret_8h'))


def _score(a: dict[str, float | None], b: dict[str, float | None]) -> float:
    assert _valid(a) and _valid(b)
    qv_a = max(float(a['qv_24h']), 1.0)
    qv_b = max(float(b['qv_24h']), 1.0)
    return (
        abs(float(a['ret_24h']) - float(b['ret_24h'])) / 0.03
        + abs(float(a['rv_24h']) - float(b['rv_24h'])) / 0.02
        + abs(math.log(qv_a / qv_b)) / 1.0
        + abs(float(a['ret_8h']) - float(b['ret_8h'])) / 0.02
    )


def has_threshold_event(
    bars: list[Kline],
    anchor: datetime,
    threshold_pct: float,
    window_minutes: int,
) -> bool:
    """Conservatively reject controls near any coarse 10% crossing."""
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
    event_metrics: dict[str, float | None],
    excluded_times: Iterable[datetime],
    controls_per_event: int = 5,
    threshold_pct: float = 10.0,
    window_minutes: int = 480,
    min_separation_days: int = 2,
) -> list[dict[str, object]]:
    excluded = list(excluded_times)
    candidates: list[dict[str, object]] = []
    # Candidate pseudo-baselines are aligned to the event's 15-minute UTC slot.
    slot_minute = (event_anchor.minute // 15) * 15
    start = bars[0].open_time + timedelta(days=10) if bars else event_anchor
    end = bars[-1].close_time - timedelta(minutes=window_minutes) if bars else event_anchor
    cursor = start.replace(hour=event_anchor.hour, minute=slot_minute, second=0, microsecond=0)
    while cursor < end:
        if abs((cursor - event_anchor).total_seconds()) < min_separation_days * 86400:
            cursor += timedelta(days=1)
            continue
        if any(abs((cursor - ts).total_seconds()) < window_minutes * 60 for ts in excluded):
            cursor += timedelta(days=1)
            continue
        if has_threshold_event(bars, cursor, threshold_pct, window_minutes):
            cursor += timedelta(days=1)
            continue
        metrics = summary_match_metrics(bars, cursor)
        if _valid(metrics) and _valid(event_metrics):
            candidates.append({
                'pseudo_baseline_time': cursor,
                'match_score': _score(event_metrics, metrics),
                **metrics,
            })
        cursor += timedelta(days=1)
    candidates.sort(key=lambda x: (float(x['match_score']), x['pseudo_baseline_time']))
    return candidates[:controls_per_event]
