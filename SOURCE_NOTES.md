# Primary source notes

- Binance Spot public market data base endpoint and REST behaviour: https://developers.binance.com/en/docs/products/spot/rest-api
- Binance Spot kline response fields include open time, open/high/low/close, quote volume and trade count: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints
- Supabase recommends TUS resumable uploads for files above 6 MB, fixed 6 MB chunks and the direct Storage hostname: https://supabase.com/docs/guides/storage/uploads/resumable-uploads
- Render Blueprint service, Docker worker and disk fields: https://render.com/docs/blueprint-spec

The application treats the Binance kline open as a timestamp-aligned trade-price benchmark. It does not claim knowledge of the exact sub-minute trade timestamp or historical order-book fill.
