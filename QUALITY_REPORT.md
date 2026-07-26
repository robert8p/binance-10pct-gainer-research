# Quality report — v1.1.2

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
- Every raw-evidence rebuild removes and verifies deletion of stale Supabase objects under its job prefix.
- Every attempt uses a unique Storage subfolder.
- Uploaded object existence and byte size are verified before the local ZIP is deleted.
- A TUS 409 is retried only if the server offset has advanced; an unchanged conflict fails immediately with a clear error.
- ZIP64 is enabled.
- Files above 6 MB use Supabase TUS resumable uploads in fixed 6 MB chunks.
- Each local shard and archive is deleted immediately after verified durable upload, preventing 20 GB Render-disk accumulation.
- Large private downloads stream through the web service instead of loading wholly into RAM.

## Automated verification

- Python compilation: passed.
- Automated tests: 21 passed.
- ZIP integrity: passed.
- Tests cover event detection, candidate detection, saleability, symbol preference, opaque Supabase secrets, resumable uploads, 409 conflict handling, recursive Storage listing, verified prefix deletion, persisted-size verification, unique attempt prefixes, neutral controls, point-in-time cutoffs, gap detection, chronological splitting and normalised SQLite construction.

## Failure corrections

### v1.1.1 disk exhaustion

The v1.1.0 exporter retained all completed local packages until the job ended. At 3,087 events this exhausted the 20 GB Render disk. v1.1.1 corrected this by deleting each local package after upload.

### v1.1.2 stale-object collision

The first v1.1.1 retry deleted database file records but left three old Supabase Storage objects. Reusing the same object path caused TUS finalisation to return `409 Conflict` and Postgres to report the `bucketid_objname` unique constraint. v1.1.2 removes and verifies all stale objects before rebuilding and writes the new run to a unique attempt prefix.
