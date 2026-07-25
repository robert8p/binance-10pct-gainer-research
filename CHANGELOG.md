# Changelog

## v1.1.0 — ChatGPT-owned pattern discovery

- Removed application-generated precursor features.
- Removed return, volatility and volume-based control matching.
- Added neutral same-symbol/time-slot/calendar controls.
- Added ten-day 15-minute and final-48-hour one-minute raw evidence.
- Added point-in-time BTC, ETH and BNB raw context.
- Added normalised SQLite evidence storage and lookahead-safe `sample_bars` view.
- Separated outcomes from predictor bars.
- Added coverage, gap, duplicate and ordering checks.
- Added chronological discovery, validation and sealed-test packages with 50-event-group sharding.
- Added resumable large-file uploads and streamed downloads.
- Added explicit ChatGPT blank-canvas analysis protocol inside every evidence package.
- Retained Supabase `sb_secret_` support and legacy service-role fallback.
