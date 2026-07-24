# External source notes

The app uses only public Binance Spot market data and does not require a Binance API key.

- Public REST market-data base: `https://data-api.binance.vision`
- Symbol universe: `GET /api/v3/exchangeInfo`
- Coarse and verification bars: `GET /api/v3/klines`
- Exact crossing and saleability: official daily Spot `aggTrades` archives from `data.binance.vision`
- Archive integrity: matching `.CHECKSUM` files are verified when available

Binance's official public-data documentation notes that Spot archive timestamps from 1 January 2025 onward are expressed in microseconds. The parser supports both the earlier millisecond format and the newer microsecond format.

Render deployment follows the current Blueprint fields for Docker web services, background workers, instance plans and persistent disks.
