# Changelog

## 1.2.0

- Replaced hindsight-selected event baselines and matched controls with a complete 15-minute candidate grid.
- Entry benchmark changed from a future local low to the timestamp-aligned interval open.
- Added every eligible negative decision to preserve the true denominator.
- Added full forward-window completeness checks so missing data cannot silently become a negative.
- Applied chronological 60/20/20 splitting before analysis.
- Added eight-hour embargoes before Validation and Sealed Test.
- Added separate entry and post-crossing exit liquidity screens, plus an explicit completeness flag so missing one-minute evidence is not relabelled as a negative.
- Removed event cooldowns and control matching; overlapping decisions remain because they are genuine decision opportunities.
- Replaced event-group exports with complete ledgers, market context and normalised subject shards.
- Added lookahead-safe `candidate_bars` and `market_decision_bars` views.
- Retained unique-attempt Storage paths, stale-prefix cleanup, upload verification and immediate local cleanup.
- Removed the dashboard's 30-file display cap.

## 1.1.2

- Fixed stale Supabase object collisions on retry.

## 1.1.1

- Fixed Render disk accumulation during evidence packaging.

## 1.1.0

- Moved feature generation out of the application, but retained a flawed hindsight event/control design. Superseded.
