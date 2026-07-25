# Quality report — v1.1.0

## Research-boundary checks

- No predictor feature module is present.
- No return, volatility, volume-ratio or technical-indicator matching is used for controls.
- Controls are selected mechanically by symbol, UTC slot, weekday priority and calendar proximity.
- Labels/outcomes are stored separately from raw bars.
- Every evidence bar must close strictly before its sample anchor.
- The anchor minute is excluded to prevent intra-minute leakage.
- Event groups and their controls remain together across discovery, validation and sealed-test splits.

## Evidence integrity

- Raw decimal values are preserved as text in SQLite rather than rounded to binary floats.
- Each bar is deduplicated by symbol, interval and open timestamp.
- `sample_windows` defines the exact point-in-time evidence available to every sample.
- The `sample_bars` view applies those windows automatically and cannot return a bar closing at or after the anchor.
- `quality` records expected and actual bar counts, coverage, gaps, duplicates and non-monotonic timestamps.
- BTCUSDT, ETHUSDT and BNBUSDT context uses the same point-in-time cutoff as the subject.
- Large splits are sharded at 50 event groups.

## Operational resilience

- Interrupted scans resume after the last processed symbol.
- Interrupted control/evidence jobs restart cleanly.
- Partial local evidence directories are removed before rebuilding.
- ZIP64 is enabled.
- Files above 6 MB use Supabase TUS resumable uploads in fixed 6 MB chunks.
- Large private downloads stream through the web service instead of loading wholly into RAM.

## Automated verification

- Python compilation: passed.
- Automated tests: 15 passed.
- Tests cover event detection, coarse candidate detection, exact saleability, symbol preference, opaque Supabase secrets, resumable uploads, neutral controls, point-in-time raw cutoffs, gap detection, chronological evidence splitting and normalised SQLite construction.
