create extension if not exists pgcrypto;

-- v1.2 uses new tables so the superseded v1.0/v1.1 research remains isolated as an audit trail.
create table if not exists binance10_grid_jobs (
  id uuid primary key default gen_random_uuid(),
  status text not null check (status in ('queued','running','completed','completed_with_warnings','failed')),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  heartbeat_at timestamptz,
  window_start_date date not null,
  window_end_date_exclusive date not null,
  lookback_days integer not null default 60,
  cadence_minutes integer not null default 15 check (cadence_minutes=15),
  threshold_pct numeric not null default 10 check (threshold_pct=10),
  horizon_minutes integer not null default 480 check (horizon_minutes=480),
  entry_liquidity_minutes integer not null default 15 check (entry_liquidity_minutes=15),
  exit_liquidity_minutes integer not null default 5 check (exit_liquidity_minutes=5),
  min_entry_notional numeric not null default 500 check (min_entry_notional>=0),
  min_exit_notional numeric not null default 500 check (min_exit_notional>=0),
  quote_assets jsonb not null default '["USDT","USDC","FDUSD"]'::jsonb,
  protocol_version text not null default 'binance10_v1_2_executable_grid',
  split_boundaries_json jsonb,
  symbols_total integer not null default 0,
  symbols_processed integer not null default 0,
  candidates_total bigint not null default 0,
  target_reached_count bigint not null default 0,
  actionable_count bigint not null default 0,
  failures integer not null default 0,
  result_json jsonb,
  error_message text,
  check (window_start_date < window_end_date_exclusive)
);

create table if not exists binance10_candidates (
  id uuid primary key default gen_random_uuid(),
  grid_job_id uuid not null references binance10_grid_jobs(id) on delete cascade,
  candidate_key text not null,
  symbol text not null,
  base_asset text not null,
  quote_asset text not null,
  decision_time timestamptz not null,
  split text not null check (split in ('discovery','validation','sealed_test')),
  entry_price numeric not null,
  entry_quote_notional numeric not null,
  entry_trade_count integer not null,
  entry_liquid boolean not null,
  target_price numeric not null,
  target_reached boolean not null,
  crossing_minute timestamptz,
  minutes_to_cross integer,
  max_forward_high numeric not null,
  max_forward_gain_pct numeric not null,
  exit_quote_notional numeric not null,
  exit_trade_count integer not null,
  exit_liquid boolean not null,
  liquidity_assessment_complete boolean not null,
  actionable_10pct boolean not null,
  label_version text not null,
  created_at timestamptz not null default now(),
  unique(grid_job_id,candidate_key)
);
-- Safe upgrade if an early v1.2 prerelease schema was applied.
alter table binance10_candidates
  add column if not exists liquidity_assessment_complete boolean not null default false;

create index if not exists idx_b10_candidates_job_split_time on binance10_candidates(grid_job_id,split,decision_time);
create index if not exists idx_b10_candidates_job_symbol_time on binance10_candidates(grid_job_id,symbol,decision_time);
create index if not exists idx_b10_candidates_outcome on binance10_candidates(grid_job_id,actionable_10pct,target_reached);

create table if not exists binance10_export_jobs (
  id uuid primary key default gen_random_uuid(),
  grid_job_id uuid not null references binance10_grid_jobs(id) on delete cascade,
  status text not null check (status in ('queued','running','completed','completed_with_warnings','failed')),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  heartbeat_at timestamptz,
  prior_days integer not null default 10 check (prior_days=10),
  high_res_hours integer not null default 48 check (high_res_hours=48),
  symbols_per_shard integer not null default 8 check (symbols_per_shard between 1 and 25),
  protocol_version text not null default 'binance10_v1_2_executable_grid',
  symbols_processed integer not null default 0,
  files_created integer not null default 0,
  raw_bar_rows bigint not null default 0,
  failures integer not null default 0,
  result_json jsonb,
  error_message text
);

create table if not exists binance10_grid_files (
  id uuid primary key default gen_random_uuid(),
  export_job_id uuid not null references binance10_export_jobs(id) on delete cascade,
  storage_path text not null,
  filename text not null,
  size_bytes bigint not null,
  sha256 text not null,
  content_type text not null,
  role text not null,
  split text check (split is null or split in ('discovery','validation','sealed_test')),
  created_at timestamptz not null default now(),
  unique(export_job_id,storage_path)
);
create index if not exists idx_b10_grid_files_job_split on binance10_grid_files(export_job_id,split,filename);

create table if not exists binance10_grid_issues (
  id bigint generated always as identity primary key,
  grid_job_id uuid references binance10_grid_jobs(id) on delete cascade,
  export_job_id uuid references binance10_export_jobs(id) on delete cascade,
  symbol text,
  stage text not null,
  message text not null,
  created_at timestamptz not null default now()
);

alter table binance10_grid_jobs enable row level security;
alter table binance10_candidates enable row level security;
alter table binance10_export_jobs enable row level security;
alter table binance10_grid_files enable row level security;
alter table binance10_grid_issues enable row level security;

insert into storage.buckets(id,name,public)
values ('binance10-research','binance10-research',false)
on conflict (id) do update set public=false;
-- No anonymous policies. The app uses SUPABASE_SECRET_KEY on trusted Render services only.
