# Binance 10% Gainer App v1.1.1 — simple deployment

Deploy this separately from the 50% and 25% applications.

## A. New deployment

### 1. GitHub

1. Create a private repository named `binance-10pct-gainer-research`.
2. Extract this ZIP.
3. Upload everything **inside** the extracted folder.
4. Confirm the repository root directly shows `app`, `supabase`, `tests`, `render.yaml` and `requirements.txt`.

### 2. Supabase

1. Create or select a Supabase project.
2. Open **SQL Editor → New query**.
3. Paste the complete contents of `supabase/schema.sql`.
4. Run it.
5. Copy:
   - project URL → `SUPABASE_URL`;
   - secret key beginning `sb_secret_` → `SUPABASE_SECRET_KEY`;
   - Session Pooler connection string on port 5432 → `DATABASE_URL`.
6. Create a strong app password → `ADMIN_PASSWORD`.

Never place any of these values in GitHub.

### 3. Render

1. Choose **New → Blueprint**.
2. Connect the GitHub repository.
3. Approve both services:
   - `binance-10pct-scanner-web`;
   - `binance-10pct-scanner-worker`.
4. Enter the same four secrets on both services:

```text
DATABASE_URL
SUPABASE_URL
SUPABASE_SECRET_KEY
ADMIN_PASSWORD
```

The worker uses a persistent disk for Binance archive caching and evidence construction.

### 4. Verify

Open:

```text
https://YOUR-WEB-SERVICE.onrender.com/health
```

Expected:

```json
{"status":"ok","version":"1.1.1","event_definition":"10pct_within_8h"}
```

Open the main URL and use any username plus `ADMIN_PASSWORD` as the password.

## B. Upgrade from v1.0.0 or v1.0.1

1. Replace the repository contents with this v1.1.1 package and commit the changes.
2. In Supabase SQL Editor, rerun the complete new `supabase/schema.sql`.
   - It safely adds the raw-evidence progress column and neutral-control metadata.
   - Existing scans and events remain intact.
3. In Render, deploy the latest commit for both services.
4. Confirm `/health` reports `1.1.1`.
5. Do not reuse a completed old feature/context job. Create a new Step 2 control job and Step 3 raw-evidence job so the evidence follows the new neutral protocol.

## C. Proof scan

Before a full scan:

1. Add `MAX_SYMBOLS=5` to the **worker only**.
2. Redeploy the worker.
3. Queue a two-day scan with minimum exit notional 500.
4. Confirm it completes without failures; zero events is acceptable.
5. Delete `MAX_SYMBOLS` and redeploy the worker.

## D. Full workflow

### Step 1 — Detect events

Use:

```text
Lookback: 60 completed UTC days
Minimum executed exit notional: 500
```

The threshold, rolling window, cooldown and saleability period are fixed at 10%, eight hours, eight hours and 300 seconds.

### Step 2 — Neutral controls

Select the completed scan and use five controls per event. Controls are selected by symbol/time/calendar eligibility only—not by engineered market features.

### Step 3 — Raw evidence export

Select the completed control job and click **Build raw evidence packages**.

This stage is materially heavier than the previous feature export because it gathers raw one-minute and 15-minute sequences. The worker streams progress and uses resumable uploads for large ZIPs.

## E. Files to provide to ChatGPT

Download:

1. `binance10_index.zip`; and
2. every discovery file listed in the index manifest, for example:

```text
binance10_discovery.zip
```

or, for a large dataset:

```text
binance10_discovery_part_001.zip
binance10_discovery_part_002.zip
...
```

Do not open or upload validation or `SEALED_TEST_DO_NOT_OPEN` packages until instructed.

Use this instruction when uploading discovery evidence:

```text
Perform a blank-canvas analysis of the attached Binance 10% raw discovery evidence. The app has not engineered predictor features or identified patterns. Read and follow CHATGPT_ANALYSIS_PROTOCOL.md. Derive candidate representations from the raw sequences, compare event groups with their controls, correct for multiple testing, assess cross-symbol and chronological stability, and report failures as well as successes. Do not inspect validation or sealed-test evidence. Finish by proposing a small set of precisely frozen candidate rules and explicit validation acceptance criteria—or conclude that no sufficiently robust relationship exists.
```

## F. Important operational notes

- Large packages are uploaded to Supabase through resumable 6 MB chunks.
- Dashboard downloads are streamed rather than loaded into web-service memory.
- Evidence packages use normalised SQLite so repeated market bars are stored once.
- Each discovery/validation/sealed shard contains at most 50 event groups to keep files manageable.
- Keep the Render worker disk until all packages have been downloaded and verified.

## Upgrade from v1.1.0 after `No space left on device`

1. Suspend the Render worker before changing code.
2. Replace the GitHub repository contents with v1.1.1 and commit.
3. Redeploy both the web service and worker; no Supabase schema change is required for this patch.
4. Confirm `/health` reports `1.1.1`.
5. Resume the worker if it remains suspended.
6. Click **Retry** on the failed raw-evidence job. The raw-evidence stage restarts from zero, but the completed scan and neutral-control job are reused.
7. Do not click Retry until the worker is definitely on v1.1.1.

The same context-job ID and storage paths are reused, so the first three partial discovery objects are overwritten during the retry.
