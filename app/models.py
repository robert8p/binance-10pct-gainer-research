from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Kline:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: datetime
    quote_volume: Decimal
    trades: int
    taker_buy_base: Decimal
    taker_buy_quote: Decimal


@dataclass(frozen=True)
class CandidateOutcome:
    decision_time: datetime
    split: str
    entry_price: Decimal
    entry_quote_notional: Decimal
    entry_trade_count: int
    entry_liquid: bool
    target_price: Decimal
    target_reached: bool
    crossing_minute: datetime | None
    minutes_to_cross: int | None
    max_forward_high: Decimal
    max_forward_gain_pct: Decimal
    exit_quote_notional: Decimal
    exit_trade_count: int
    exit_liquid: bool
    liquidity_assessment_complete: bool
    actionable_10pct: bool
