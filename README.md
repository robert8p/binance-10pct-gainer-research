# Binance 10% Gainer Research App

A read-only Binance Spot research pipeline that finds **saleable 10% rises within eight hours**, creates matched non-event controls, and exports ten days of point-in-time precursor features for analysis.

## Fixed event definition

An event occurs when a completed one-minute bar's high first reaches at least **110% of the lowest low in the preceding completed 480 minutes**. The current bar's low cannot become its own baseline. A symbol then enters an eight-hour cooldown so one move is not counted repeatedly.

The scan uses 15-minute bars to shortlist candidate ranges, then one-minute bars to verify the exact crossing. Saleability is tested using executed aggregate-trade quote notional during the first 300 seconds after crossing; one-minute quote volume is used only when the official daily aggregate-trade archive is not yet available.

## Workflow

1. Scan the current Binance Spot universe for USDT, USDC and FDUSD pairs.
2. Retain events with at least 500 units of executed quote notional in the five-minute exit window.
3. Build up to five same-symbol non-event controls for every saleable event.
4. Match on prior 24-hour return, volatility, quote volume and prior eight-hour return.
5. Build multi-window precursor features at snapshots from ten days before the baseline through the final completed bar before baseline, including contemporaneous BTC, ETH and BNB market context.
6. Split event groups chronologically into discovery, validation and sealed-test datasets, keeping each event and its controls together.
7. Export separate index, discovery, validation and `SEALED_TEST_DO_NOT_OPEN` ZIPs to private Supabase Storage.

## Boundaries

- No trading, wallet access, API key or order placement.
- Initial historical coverage uses the current tradeable Binance universe and can miss delisted symbols.
- Saleability is an evidence screen, not a fill guarantee; historical order-book queues are unavailable.
- The package finds candidate relationships. It does not claim a profitable rule.

See `DEPLOYMENT_GUIDE.md` for copy/paste deployment steps.

## Evidence handling

Download the index and discovery packages first. Do not inspect validation until discovery rules and acceptance thresholds are frozen. Do not inspect the sealed test unless validation passes without retuning.
