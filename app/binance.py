from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO, TextIOWrapper
import hashlib
from pathlib import Path
import time
from typing import Iterable
from zipfile import ZipFile

import requests

from .config import Settings
from .models import Kline

UTC = timezone.utc


class BinanceError(RuntimeError):
    pass


def _to_datetime(value: int | str) -> datetime:
    raw = int(value)
    # Binance Spot archive timestamps are microseconds from 2025-01-01 onward.
    divisor = 1_000_000 if raw > 10_000_000_000_000 else 1_000
    return datetime.fromtimestamp(raw / divisor, tz=UTC)


class BinanceClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'binance-10pct-research/1.2'})

    def _get_json(self, path: str, params: dict[str, object] | None = None) -> object:
        url = f'{self.settings.public_api_base}{path}'
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = self.session.get(url, params=params, timeout=self.settings.request_timeout_seconds)
                if response.status_code in {418, 429}:
                    retry = float(response.headers.get('Retry-After', 2 + attempt * 2))
                    time.sleep(min(retry, 30))
                    continue
                response.raise_for_status()
                time.sleep(self.settings.request_pause_seconds)
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(min(2 ** attempt, 15))
        raise BinanceError(f'Binance request failed: {url}: {last_error}')

    def active_spot_symbols(self, quote_assets: Iterable[str]) -> list[dict[str, str]]:
        payload = self._get_json('/api/v3/exchangeInfo')
        ordered_quotes = tuple(quote_assets)
        wanted = set(ordered_quotes)
        symbols: list[dict[str, str]] = []
        for row in payload.get('symbols', []):  # type: ignore[union-attr]
            if row.get('status') != 'TRADING':
                continue
            if row.get('isSpotTradingAllowed') is False:
                continue
            quote = str(row.get('quoteAsset', '')).upper()
            if quote not in wanted:
                continue
            permissions = row.get('permissions') or []
            if permissions and 'SPOT' not in permissions:
                continue
            symbols.append({
                'symbol': str(row['symbol']).upper(),
                'base_asset': str(row['baseAsset']).upper(),
                'quote_asset': quote,
            })
        symbols.sort(key=lambda x: (wanted_order(x['quote_asset'], ordered_quotes), x['symbol']))
        # Use one preferred quote pair per base asset so the same underlying
        # surge is not counted independently in USDT, USDC and FDUSD.
        preferred_by_base: dict[str, dict[str, str]] = {}
        for row in symbols:
            preferred_by_base.setdefault(row['base_asset'], row)
        return sorted(preferred_by_base.values(), key=lambda x: x['symbol'])

    def klines(self, symbol: str, interval: str, start: datetime, end: datetime) -> list[Kline]:
        rows: list[Kline] = []
        cursor_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        while cursor_ms < end_ms:
            payload = self._get_json('/api/v3/klines', {
                'symbol': symbol,
                'interval': interval,
                'startTime': cursor_ms,
                'endTime': end_ms - 1,
                'limit': 1000,
            })
            if not payload:
                break
            page = [parse_kline(row) for row in payload]  # type: ignore[arg-type]
            rows.extend(page)
            next_ms = int(page[-1].close_time.timestamp() * 1000) + 1
            if next_ms <= cursor_ms:
                break
            cursor_ms = next_ms
        # API responses can overlap by a millisecond boundary; de-duplicate deterministically.
        dedup = {row.open_time: row for row in rows if start <= row.open_time < end}
        return [dedup[key] for key in sorted(dedup)]

    def _archive_path(self, symbol: str, day: date) -> tuple[Path, str]:
        filename = f'{symbol}-aggTrades-{day.isoformat()}.zip'
        url = f'{self.settings.archive_base}/data/spot/daily/aggTrades/{symbol}/{filename}'
        cache_dir = self.settings.temp_data_dir / 'aggTrades' / symbol
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / filename
        if not cached.exists():
            response = self.session.get(url, timeout=max(self.settings.request_timeout_seconds, 90))
            if response.status_code == 404:
                raise FileNotFoundError(url)
            response.raise_for_status()
            cached.write_bytes(response.content)
        digest = hashlib.sha256(cached.read_bytes()).hexdigest()
        checksum_response = self.session.get(f'{url}.CHECKSUM', timeout=self.settings.request_timeout_seconds)
        if checksum_response.status_code == 200:
            expected = checksum_response.text.strip().split()[0].lower()
            if expected and expected != digest.lower():
                cached.unlink(missing_ok=True)
                raise BinanceError(f'Checksum mismatch for {url}: expected {expected}, got {digest}')
        return cached, digest

    def _iter_archived_aggtrades(self, symbol: str, day: date):
        cached, digest = self._archive_path(symbol, day)
        with ZipFile(cached) as archive:
            names = [name for name in archive.namelist() if not name.endswith('/')]
            if not names:
                raise BinanceError(f'Empty archive: {cached}')
            with archive.open(names[0]) as raw:
                reader = csv.reader(TextIOWrapper(raw, encoding='utf-8'))
                for row in reader:
                    if not row or not row[0].lstrip('-').isdigit():
                        continue
                    yield _to_datetime(row[5]), Decimal(row[1]), Decimal(row[2]), digest

    def archived_saleability(
        self,
        symbol: str,
        crossing_minute: datetime,
        threshold_price: Decimal,
        saleability_seconds: int,
    ) -> tuple[datetime, Decimal, int, list[str]]:
        """Reconstruct the first threshold trade and following executed notional.

        Only trades at or after the exact threshold crossing are counted. The
        iterator streams official daily archives, so a large liquid-symbol file
        is not retained in memory.
        """
        search_end = crossing_minute + timedelta(minutes=1)
        latest_needed = search_end + timedelta(seconds=saleability_seconds)
        day = crossing_minute.date()
        final_day = latest_needed.date()
        exact_cross: datetime | None = None
        exit_end: datetime | None = None
        quote_notional = Decimal('0')
        trade_count = 0
        digests: list[str] = []
        while day <= final_day:
            day_digest: str | None = None
            for ts, price, qty, digest in self._iter_archived_aggtrades(symbol, day):
                day_digest = digest
                if ts < crossing_minute:
                    continue
                if exact_cross is None:
                    if ts >= search_end:
                        break
                    if price < threshold_price:
                        continue
                    exact_cross = ts
                    exit_end = exact_cross + timedelta(seconds=saleability_seconds)
                if exit_end is not None and exact_cross <= ts < exit_end:
                    quote_notional += price * qty
                    trade_count += 1
                elif exit_end is not None and ts >= exit_end:
                    break
            if day_digest and day_digest not in digests:
                digests.append(day_digest)
            if exact_cross is not None and exit_end is not None:
                day_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
                if exit_end <= day_end:
                    break
            day += timedelta(days=1)
        if exact_cross is None:
            raise BinanceError(f'No aggregate trade crossed {threshold_price} in {symbol} minute {crossing_minute.isoformat()}')
        return exact_cross, quote_notional, trade_count, digests


def wanted_order(value: str, ordered: tuple[str, ...]) -> int:
    try:
        return ordered.index(value)
    except ValueError:
        return len(ordered)


def parse_kline(row: list[object]) -> Kline:
    return Kline(
        open_time=_to_datetime(row[0]),
        open=Decimal(str(row[1])),
        high=Decimal(str(row[2])),
        low=Decimal(str(row[3])),
        close=Decimal(str(row[4])),
        volume=Decimal(str(row[5])),
        close_time=_to_datetime(row[6]),
        quote_volume=Decimal(str(row[7])),
        trades=int(row[8]),
        taker_buy_base=Decimal(str(row[9])),
        taker_buy_quote=Decimal(str(row[10])),
    )


def completed_window(lookback_days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(tz=UTC)
    end = datetime.combine(current.date(), datetime.min.time(), tzinfo=UTC)
    return end - timedelta(days=lookback_days), end
