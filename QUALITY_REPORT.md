# Quality report — v1.0.0

## Research-integrity design

- The event baseline uses only completed minutes before the crossing minute.
- A symbol is suppressed for eight hours after an event to avoid repeated counting of one surge episode.
- Coarse 15-minute bars only shortlist ranges; one-minute bars determine the qualifying crossing.
- Saleability reconstructs the exact first aggregate trade at or above the threshold, counts only following executed notional, verifies official archive checksums when available, and falls back visibly to one-minute quote volume only where an archive is unavailable.
- Controls are same-symbol and same-UTC-slot, separated from events and screened for contaminating 10% moves.
- BTC, ETH and BNB reference features are calculated with the same completed-bar cutoff as the subject coin.
- Predictor rows use only fully completed 15-minute bars and stop before each decision timestamp, including when the event baseline occurs mid-bar.
- Outcome fields are separated into `samples.csv` and prefixed `outcome_`.
- Event groups are split chronologically into physically separate discovery, validation and sealed-test ZIPs.
- The 10% threshold, eight-hour window and ten-day history are fixed in schema and code.

## Automated checks included

- Millisecond and microsecond timestamp handling.
- First-crossing detection and cooldown.
- Current-bar low exclusion from the baseline.
- Predictor cutoff before the decision timestamp, including a mid-bar leakage test.
- Deterministic event-group split coverage.
- Python compilation and pytest in GitHub Actions.

## Important limitations

- Live Binance, Supabase and Render calls cannot be tested without the user's credentials and deployed environment.
- Current-universe survivorship bias remains for delisted historical assets.
- A five-minute executed-notional screen does not reproduce historical spreads, queue position or market impact.
- At 10%, event counts may be much larger than the 50% app; runtime, storage and control availability should be assessed after the proof scan.

## Operational resilience

- A restarted worker resumes scans after the last fully processed symbol.
- Interrupted control and context jobs restart cleanly to avoid mixed partial outputs.
- Failed jobs expose a dashboard retry action.
