# Upgrade deployment — v1.2.0

v1.2.0 uses new Supabase tables. The old v1.0/v1.1 tables and files remain untouched as an audit trail but are no longer read by the app.

## 1. Stop the worker

In Render, suspend `binance-10pct-scanner-worker`. Confirm new worker logs stop appearing.

## 2. Replace the GitHub repository contents

1. Extract the v1.2.0 ZIP.
2. Delete the existing repository contents, except `.git` when working locally.
3. Upload everything inside the extracted folder to the repository root.
4. Confirm `app`, `supabase`, `tests`, `render.yaml`, `Dockerfile` and `requirements.txt` are visible at repository root.
5. Commit: `Replace leaked event-control design with executable candidate grid v1.2.0`.

## 3. Create the v1.2 Supabase tables

1. Open Supabase → SQL Editor → New query.
2. Open `supabase/schema.sql` from this package.
3. Paste the entire file and select **Run**.
4. The script creates `binance10_grid_jobs`, `binance10_candidates`, `binance10_export_jobs`, `binance10_grid_files` and `binance10_grid_issues`.
5. Do not delete the old tables; they document the invalidated v1.1 run.

## 4. Deploy the web service

Deploy the latest commit to `binance-10pct-scanner-web`.

Open `/health`. Required response:

```json
{
  "status": "ok",
  "version": "1.2.0",
  "event_definition": "executable_entry_10pct_within_8h_on_complete_15m_grid"
}
```

## 5. Deploy and resume the worker

Deploy the same commit to `binance-10pct-scanner-worker`, then resume it. Required secrets on both services:

- `DATABASE_URL`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `ADMIN_PASSWORD`

Do not use a publishable/anon key.

## 6. Proof run

Temporarily add this environment variable to the worker:

```text
MAX_SYMBOLS=5
```

Rebuild and deploy the worker. In the dashboard queue a 10-day candidate grid. Wait for completion and confirm:

- five symbols processed;
- candidates include both positive and negative outcomes;
- candidate timestamps are all quarter-hour boundaries;
- the job has no failures.

Queue an export only after the proof grid completes. Confirm the index, Discovery ledger, Discovery market file and Discovery subject parts appear.

## 7. Full run

Delete `MAX_SYMBOLS`, rebuild and deploy the worker, then queue a new 60-day grid. Do not reuse the proof job.

After the grid completes, queue one raw evidence export. The export will take substantial time because it retains the complete candidate denominator and unique raw one-minute bars.

## 8. Files to return for analysis

Upload only:

- `binance10_index.zip`
- `binance10_discovery_ledger.zip`
- `binance10_discovery_market.zip`
- every `binance10_discovery_subject_part_*.zip`

Do not open or upload Validation or any file beginning `SEALED_TEST_DO_NOT_OPEN` until requested.

## Retry behaviour

- A worker restart resumes an interrupted candidate-grid scan from the next symbol.
- A manual grid retry clears the incomplete grid and starts it from zero.
- An export retry removes stale Supabase objects, starts a unique attempt folder and reconstructs the export from zero.
