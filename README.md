# Binance 10% Executable Grid Research — v1.2.0

This release replaces the hindsight-selected event/control design with a complete timestamp-aligned research population.

## Research boundary

The application performs data engineering and outcome labelling only. It does **not** engineer predictor features, select patterns, fit models or create trading rules. ChatGPT owns those analytical steps after export.

## Candidate protocol

For every currently tradeable Binance Spot base asset, using one preferred quote pair (USDT, then USDC, then FDUSD), the app evaluates every eligible UTC 15-minute boundary.

A candidate is eligible only when:

- the entry interval has at least one trade;
- its complete eight-hour forward outcome window is present;
- it is not inside a chronological split embargo.

The entry benchmark is the 15-minute kline open: the first available trade-price benchmark for that interval. This removes the previous hindsight-selected local low. The exact sub-minute trade timestamp is not available from the kline and the protocol records that limitation.

The app labels:

- whether price reaches +10% from entry within eight hours;
- entry quote volume in the entry 15-minute interval;
- the first one-minute crossing interval;
- quote volume in the next five full one-minute intervals after crossing;
- `liquidity_assessment_complete`, so missing one-minute exit evidence is never silently treated as a confirmed negative;
- a primary `actionable_10pct` label requiring the target, complete liquidity assessment and both liquidity screens.

The liquidity fields are screens, not historical order-book fill reconstructions.

## Chronological evidence separation

The requested period is split before analysis:

- first 60%: Discovery;
- next 20%: Validation;
- final 20%: Sealed Test.

An eight-hour embargo is removed before the Validation and Sealed Test boundaries so a forward label cannot cross into the next split.

## Export contents

Each split receives:

- a complete candidate ledger containing every eligible decision and its outcome;
- a market-context package for BTCUSDT, ETHUSDT and BNBUSDT;
- subject evidence shards containing raw bars for the relevant symbols.

Raw predictor history is:

- ten days at 15-minute resolution;
- 48 hours at one-minute resolution.

`candidate_bars` and `market_decision_bars` end strictly before the decision timestamp. Outcomes remain in separate tables.

## Operational safeguards

- Large ZIPs use Supabase TUS resumable uploads in fixed 6 MB chunks.
- Every export attempt uses a unique Storage prefix.
- Stale attempt objects are deleted and verified before retry.
- Uploaded byte size is verified before local files are removed.
- Each completed local shard is deleted immediately, preventing Render disk accumulation.
- The dashboard lists all generated files rather than only the latest 30.

## Known limitations

- The scan starts from the current Binance Spot universe and can omit pairs delisted before the scan date.
- A kline open provides a trade-price benchmark but not the exact sub-minute timestamp.
- Historical trade volume does not reconstruct order-book depth, queue position, fees or slippage.
- Current-symbol selection and historical survivorship must be considered when interpreting results.

Read `DEPLOYMENT_GUIDE.md` before replacing an existing deployment.
