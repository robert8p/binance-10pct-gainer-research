from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import math
from statistics import mean, pstdev

from .models import Kline

WINDOWS_MINUTES = (15, 30, 60, 120, 180, 360, 480, 720, 1440, 2880, 4320, 7200, 10080, 14400)
SNAPSHOT_OFFSETS_MINUTES = (14400, 10080, 7200, 4320, 2880, 1440, 720, 480, 360, 180, 60, 0)
REFERENCE_WINDOWS_MINUTES = (60, 180, 480, 1440, 4320, 10080)


def _float(value: Decimal) -> float:
    return float(value)


def _bars_between(bars: list[Kline], start: datetime, end: datetime) -> list[Kline]:
    # A bar is usable only after it has fully closed. This matters when an
    # event baseline occurs part-way through a 15-minute bar.
    return [bar for bar in bars if bar.open_time >= start and bar.close_time < end]


def _safe_return(first: Decimal, last: Decimal) -> float | None:
    if first <= 0:
        return None
    return float(last / first - Decimal('1'))


def _realised_vol(closes: list[Decimal]) -> float | None:
    if len(closes) < 3:
        return None
    returns = [math.log(float(closes[i] / closes[i - 1])) for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    if len(returns) < 2:
        return None
    return pstdev(returns) * math.sqrt(len(returns))


def feature_row(
    sample_id: str,
    label: str,
    symbol: str,
    anchor_time: datetime,
    bars: list[Kline],
    snapshot_offset_minutes: int,
    reference_bars: dict[str, list[Kline]] | None = None,
) -> dict[str, object]:
    decision_time = anchor_time - timedelta(minutes=snapshot_offset_minutes)
    prior = [bar for bar in bars if bar.close_time < decision_time]
    row: dict[str, object] = {
        'sample_id': sample_id,
        'label': label,
        'symbol': symbol,
        'anchor_time': anchor_time.isoformat(),
        'snapshot_offset_minutes': snapshot_offset_minutes,
        'decision_time': decision_time.isoformat(),
        'data_cutoff_time': prior[-1].close_time.isoformat() if prior else None,
    }
    if not prior:
        row['feature_available'] = False
        return row
    row['feature_available'] = True
    last = prior[-1]
    row.update({
        'price': _float(last.close),
        'bar_quote_volume': _float(last.quote_volume),
        'bar_trade_count': last.trades,
        'bar_taker_buy_share': float(last.taker_buy_quote / last.quote_volume) if last.quote_volume > 0 else None,
        'utc_hour': decision_time.hour,
        'utc_weekday': decision_time.weekday(),
    })

    for minutes in WINDOWS_MINUTES:
        window = _bars_between(prior, decision_time - timedelta(minutes=minutes), decision_time)
        prefix = f'w{minutes}'
        if len(window) < 2:
            row[f'{prefix}_available'] = False
            continue
        closes = [x.close for x in window]
        highs = [x.high for x in window]
        lows = [x.low for x in window]
        quote_volumes = [float(x.quote_volume) for x in window]
        trades = [x.trades for x in window]
        taker_buy = sum((x.taker_buy_quote for x in window), Decimal('0'))
        qv = sum((x.quote_volume for x in window), Decimal('0'))
        row.update({
            f'{prefix}_available': True,
            f'{prefix}_return': _safe_return(window[0].open, window[-1].close),
            f'{prefix}_range': float(max(highs) / min(lows) - Decimal('1')) if min(lows) > 0 else None,
            f'{prefix}_realised_vol': _realised_vol(closes),
            f'{prefix}_quote_volume': sum(quote_volumes),
            f'{prefix}_mean_bar_quote_volume': mean(quote_volumes),
            f'{prefix}_quote_volume_cv': pstdev(quote_volumes) / mean(quote_volumes) if len(quote_volumes) > 1 and mean(quote_volumes) > 0 else None,
            f'{prefix}_trades': sum(trades),
            f'{prefix}_taker_buy_share': float(taker_buy / qv) if qv > 0 else None,
            f'{prefix}_up_bar_share': sum(1 for x in window if x.close > x.open) / len(window),
            f'{prefix}_max_bar_return': max(float(x.high / x.open - Decimal('1')) for x in window if x.open > 0),
            f'{prefix}_min_bar_return': min(float(x.low / x.open - Decimal('1')) for x in window if x.open > 0),
        })
    for ref_name, ref_bars in (reference_bars or {}).items():
        ref_prior = [bar for bar in ref_bars if bar.close_time < decision_time]
        for minutes in REFERENCE_WINDOWS_MINUTES:
            ref_window = _bars_between(ref_prior, decision_time - timedelta(minutes=minutes), decision_time)
            ref_prefix = f'ref_{ref_name}_w{minutes}'
            if len(ref_window) < 2:
                row[f'{ref_prefix}_available'] = False
                continue
            ref_return = _safe_return(ref_window[0].open, ref_window[-1].close)
            ref_lows = [x.low for x in ref_window]
            ref_highs = [x.high for x in ref_window]
            row.update({
                f'{ref_prefix}_available': True,
                f'{ref_prefix}_return': ref_return,
                f'{ref_prefix}_range': float(max(ref_highs) / min(ref_lows) - Decimal('1')) if min(ref_lows) > 0 else None,
                f'{ref_prefix}_realised_vol': _realised_vol([x.close for x in ref_window]),
                f'{ref_prefix}_quote_volume': sum(float(x.quote_volume) for x in ref_window),
            })
            subject_return = row.get(f'w{minutes}_return')
            if isinstance(subject_return, (int, float)) and isinstance(ref_return, (int, float)):
                row[f'w{minutes}_excess_return_vs_{ref_name}'] = subject_return - ref_return
    return row


def summary_match_metrics(bars: list[Kline], anchor: datetime) -> dict[str, float | None]:
    prior_24h = _bars_between(bars, anchor - timedelta(hours=24), anchor)
    prior_8h = _bars_between(bars, anchor - timedelta(hours=8), anchor)
    if len(prior_24h) < 10 or len(prior_8h) < 4:
        return {'ret_24h': None, 'rv_24h': None, 'qv_24h': None, 'ret_8h': None}
    return {
        'ret_24h': _safe_return(prior_24h[0].open, prior_24h[-1].close),
        'rv_24h': _realised_vol([x.close for x in prior_24h]),
        'qv_24h': sum(float(x.quote_volume) for x in prior_24h),
        'ret_8h': _safe_return(prior_8h[0].open, prior_8h[-1].close),
    }
