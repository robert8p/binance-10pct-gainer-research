# Changelog

## 1.1.2 — collision-safe raw-evidence retries

- Deletes every stale Supabase Storage object beneath the context-job prefix before a raw-evidence rebuild starts.
- Verifies the prefix is empty before downloading and packaging hundreds of millions of bar references.
- Writes each retry to a unique `attempt_<uuid>` Storage folder, so an earlier failed attempt cannot collide with a new filename.
- Verifies every uploaded object exists in Supabase and has the exact expected byte size before registering it or deleting the local copy.
- Treats a TUS `409 Conflict` as fatal unless the server upload offset has genuinely advanced; it no longer repeats the same conflicting PATCH five times.
- Retains the bounded-disk behaviour introduced in v1.1.1.

## 1.1.1 — bounded-disk evidence finalisation

- Deletes each local uncompressed shard and ZIP immediately after its successful Supabase upload.
- Prevents uploaded parts from accumulating on the 20 GB Render disk while later parts are compressed.
- Streams SHA-256 calculation so multi-GB ZIPs are not loaded fully into memory.
- Removes the finished job directory after the index upload.

## 1.1.0 — ChatGPT-owned pattern discovery

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
