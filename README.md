# Binance 10% Gainer Research App — v1.1.2

A read-only Binance Spot evidence pipeline for investigating **saleable 10% rises within eight hours**.

## Critical design boundary

The app does **not** identify precursor patterns. It does not calculate returns, volatility, volume ratios, technical indicators, machine-learning features or trading rules.

Its responsibilities are limited to:

1. detecting events using a fixed definition;
2. checking historical exit liquidity;
3. selecting neutral same-symbol non-event controls;
4. collecting point-in-time raw exchange data;
5. checking coverage and gaps;
6. separating discovery, validation and sealed-test evidence; and
7. packaging the evidence for ChatGPT.

ChatGPT is responsible for deriving representations, generating hypotheses, testing them, interpreting the evidence and freezing candidate rules.

## Fixed event definition

An event occurs when a completed one-minute bar's high first reaches at least **110% of the lowest low in the preceding completed 480 minutes**. The crossing bar cannot provide its own baseline. A symbol then enters an eight-hour cooldown so one move is not counted repeatedly.

The scan uses 15-minute bars to shortlist candidate ranges and one-minute bars to verify them. Saleability is tested using aggregate-trade quote notional during the first 300 seconds after the exact crossing. One-minute quote volume is used only when the official daily aggregate-trade archive is unavailable.

## Neutral controls

For each saleable event, the app seeks up to five controls that are:

- the same Binance symbol;
- aligned to the same UTC 15-minute time slot;
- outside nearby event windows; and
- prioritised by the same weekday, then nearest eligible calendar date.

Controls are **not** matched on prior returns, volatility, volume, trade count or any proposed predictor. This prevents the app from pre-deciding which market characteristics matter.

## Raw evidence profile

For each event and control anchor:

- ten days of completed 15-minute subject bars;
- the final 48 hours of completed one-minute subject bars;
- the same two resolutions for BTCUSDT, ETHUSDT and BNBUSDT;
- OHLC prices;
- base and quote volume;
- trade count;
- taker-buy base and quote volume; and
- point-in-time coverage and gap checks.

The anchor minute itself is excluded. Every retained bar closes strictly before the sample anchor.

## Normalised SQLite packages

Each evidence ZIP contains `raw_evidence.sqlite` with these tables:

- `samples` — sample identity, symbol and anchor;
- `outcomes` — labels and event outcomes, kept separate from raw bars;
- `sample_windows` — the exact subject/reference window belonging to each sample;
- `bars` — each raw Binance bar stored once by symbol, interval and timestamp; and
- `quality` — coverage, gaps, duplicates and ordering checks; and
- `sample_bars` — a read-only view that applies each sample's exact window and strict pre-anchor cutoff automatically.

Small CSV copies of the metadata tables are included for easy inspection. Raw bars remain in SQLite to avoid duplicating the same BTC, ETH, BNB or subject bars across many samples.

Evidence is split chronologically by event group: 60% discovery, 20% validation and 20% sealed test. Each event remains with all of its controls. Large splits are sharded into packages containing at most 50 event groups.

## Workflow

1. Run the historical event scan.
2. Build neutral controls.
3. Build raw evidence packages.
4. Download `binance10_index.zip` and every discovery part listed in its manifest.
5. Upload those files to ChatGPT for blank-canvas discovery.
6. Freeze candidate rules and validation acceptance criteria.
7. Open validation without changing the rules.
8. Open the sealed test only if validation passes without retuning.

## Boundaries and limitations

- No trading, wallet access, order placement or Binance account API key.
- The current tradeable Binance universe can omit historically delisted assets.
- Historical order-book queues are unavailable; saleability is an evidence screen, not a fill guarantee.
- Controls are observational and cannot establish causality.
- One-minute raw history is limited to the final 48 hours to keep evidence tractable; the full ten days remain available at 15-minute resolution.
- The app can find data. Only subsequent analysis can determine whether a repeatable, economically useful relationship exists.

See `DEPLOYMENT_GUIDE.md` for deployment and upgrade steps.



## v1.1.2 retry protection

A raw-evidence retry now performs a fail-fast Storage preflight before rebuilding:

1. recursively lists every object under `raw-evidence/<context-job-id>/`;
2. removes all stale objects through the Supabase Storage API;
3. verifies that the prefix is empty;
4. creates a new unique `attempt_<uuid>` folder;
5. uploads each package into that attempt folder;
6. verifies the persisted Supabase object size before registering the download and deleting the local copy.

A stale object can therefore no longer produce the duplicate-key `409 Conflict` seen when v1.1.1 retried a partially uploaded job. Manual deletion in the Supabase dashboard is not required when the worker is running v1.1.2.
