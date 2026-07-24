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
class DetectedEvent:
    symbol: str
    base_asset: str
    quote_asset: str
    baseline_time: datetime
    baseline_price: Decimal
    crossing_time: datetime
    crossing_bar_open: Decimal
    crossing_bar_high: Decimal
    threshold_price: Decimal
    gain_pct: Decimal
    minutes_to_cross: int
    exit_quote_notional: Decimal
    exit_trade_count: int
    saleability_source: str
    saleable: bool
