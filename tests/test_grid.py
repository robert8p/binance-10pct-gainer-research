from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.grid import candidate_times, coarse_positive_times, evaluate_candidate, overlapping_groups
from app.models import Kline
from app.protocol import split_boundaries

UTC = timezone.utc


def bar(ts, minutes, *, open='100', high='100', low='100', close='100', quote='1000', trades=10):
    return Kline(
        ts, Decimal(open), Decimal(high), Decimal(low), Decimal(close), Decimal('10'),
        ts + timedelta(minutes=minutes) - timedelta(microseconds=1), Decimal(quote), trades,
        Decimal('5'), Decimal('500'),
    )


def test_events_and_non_events_share_identical_grid_timestamps():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=20)
    boundaries = split_boundaries(start, end)
    bars = [bar(start + timedelta(minutes=15 * i), 15) for i in range(20 * 96 + 40)]
    decisions = candidate_times(bars, start, end, boundaries)
    assert decisions
    assert all(value.minute % 15 == 0 for value in decisions)


def test_entry_uses_interval_open_not_hindsight_low():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    bars15 = [
        bar(t, 15, open='100', high='103', low='80', close='90', quote='2000'),
        bar(t + timedelta(minutes=15), 15, open='90', high='111', low='89', close='110'),
    ]
    bars1 = [bar(t + timedelta(minutes=i), 1, open='100', high='111' if i == 15 else '103', quote='200') for i in range(22)]
    outcome = evaluate_candidate(t, 'discovery', bars15, bars1)
    assert outcome.entry_price == Decimal('100')
    assert outcome.target_price == Decimal('110.0')
    assert outcome.target_reached is True
    assert outcome.crossing_minute == t + timedelta(minutes=15)
    assert outcome.exit_quote_notional == Decimal('1000')
    assert outcome.actionable_10pct is True


def test_negative_candidate_needs_no_one_minute_fetch():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    bars15 = [bar(t + timedelta(minutes=15 * i), 15, open='100', high='109') for i in range(32)]
    outcome = evaluate_candidate(t, 'discovery', bars15, None)
    assert outcome.target_reached is False
    assert outcome.actionable_10pct is False


def test_coarse_prescreen_and_overlapping_groups():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [bar(t + timedelta(minutes=15 * i), 15, open='100', high='100') for i in range(80)]
    # Candidate at t crosses in bar 10; candidate at t+15 crosses too.
    bars[10] = bar(t + timedelta(minutes=150), 15, open='100', high='111')
    decisions = [t, t + timedelta(minutes=15), t + timedelta(hours=12)]
    positives = coarse_positive_times(bars, decisions, threshold_pct=Decimal('10'), horizon_minutes=480)
    assert t in positives
    assert t + timedelta(minutes=15) in positives
    groups = overlapping_groups([t, t + timedelta(minutes=15), t + timedelta(hours=12)], 480)
    assert len(groups) == 2

def test_incomplete_forward_window_is_not_an_eligible_negative():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=20)
    boundaries = split_boundaries(start, end)
    # Only ten future bars exist, fewer than the required 32.
    bars = [bar(start + timedelta(minutes=15 * i), 15) for i in range(10)]
    assert candidate_times(bars, start, end, boundaries) == []


def test_coarse_target_is_not_relabelled_negative_when_one_minute_liquidity_is_missing():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    bars15 = [bar(t + timedelta(minutes=15 * i), 15, open='100', high='111' if i == 4 else '105') for i in range(32)]
    outcome = evaluate_candidate(t, 'discovery', bars15, [])
    assert outcome.target_reached is True
    assert outcome.liquidity_assessment_complete is False
    assert outcome.actionable_10pct is False
