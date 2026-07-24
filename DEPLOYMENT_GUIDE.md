# Simple deployment from scratch

Deploy this as a **separate app** from the 50% scanner. Do not overwrite the working 50% repository or its database tables.

## 1. Create a private GitHub repository

Create a private repository named `binance-10pct-gainer-research`. Extract this ZIP and upload everything **inside** the extracted folder. The repository root must show `app`, `supabase`, `tests`, `render.yaml` and `requirements.txt`.

## 2. Create or select a Supabase project

A separate Supabase project is safest. In **SQL Editor → New query**, paste all of `supabase/schema.sql` and run it once. This creates the `binance10_*` tables and private `binance10-research` bucket.

Copy these values:

- Project URL → `SUPABASE_URL`
- Service-role key → `SUPABASE_SERVICE_ROLE_KEY`
- Database session-pooler connection string → `DATABASE_URL`

Use the service-role key only in Render. Never put it in GitHub.

## 3. Deploy the Render Blueprint

In Render choose **New → Blueprint**, connect the GitHub repository and approve both services:

- `binance-10pct-scanner-web`
- `binance-10pct-scanner-worker`

The worker requests Pro plus a 20 GB disk because the official aggregate-trade archives are cached. You can reduce the plan after the historical work is complete, but a large scan may then run much more slowly.

Set the same secrets on both services:

```text
DATABASE_URL
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
ADMIN_PASSWORD
```

## 4. Verify

Open the web service `/health` path. Expected:

```json
{"status":"ok","version":"1.0.0","event_definition":"10pct_within_8h"}
```

Open the dashboard and log in using any username plus the `ADMIN_PASSWORD` as the password.

## 5. Run a proof scan first

Before the 60-day run, temporarily add this worker environment variable:

```text
MAX_SYMBOLS=5
```

Queue a two-day historical scan with minimum exit notional 500. Confirm it completes and records events or zero events without failures. Then remove `MAX_SYMBOLS`, redeploy the worker, and run the full scan.

## 6. Run the research workflow

Use:

```text
Lookback: 60 completed UTC days
Threshold: fixed at 10%
Rolling window: fixed at 8 hours
Cooldown: fixed at 8 hours
Minimum exit notional: 500
Saleability window: fixed at 300 seconds
Controls per event: 5
Predictor history: fixed at 10 days
```

After Step 3 completes, download `binance10_index.zip` and `binance10_discovery.zip` and upload those two files to ChatGPT. Leave the validation and `SEALED_TEST_DO_NOT_OPEN.zip` packages unopened until instructed.
