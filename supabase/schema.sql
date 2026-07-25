create extension if not exists pgcrypto;

create table if not exists binance10_scan_jobs (
  id uuid primary key default gen_random_uuid(),
  status text not null check (status in ('queued','running','completed','completed_with_warnings','failed')),
  created_at timestamptz not null default now(), started_at timestamptz, completed_at timestamptz, heartbeat_at timestamptz,
  window_start_date date not null, window_end_date_exclusive date not null, lookback_days integer not null default 60,
  threshold_pct numeric not null default 10 check (threshold_pct = 10),
  window_minutes integer not null default 480 check (window_minutes = 480),
  cooldown_minutes integer not null default 480 check (cooldown_minutes >= 480),
  quote_assets jsonb not null default '["USDT","USDC","FDUSD"]'::jsonb,
  min_exit_notional numeric not null default 500 check (min_exit_notional >= 0),
  saleability_seconds integer not null default 300 check (saleability_seconds = 300),
  event_definition_version text not null default 'binance10_v1_rolling_8h',
  symbols_total integer not null default 0, symbols_processed integer not null default 0,
  events_found integer not null default 0, saleable_events integer not null default 0, failures integer not null default 0,
  result_json jsonb, error_message text,
  check (window_start_date < window_end_date_exclusive)
);

create table if not exists binance10_events (
  id uuid primary key default gen_random_uuid(), scan_job_id uuid not null references binance10_scan_jobs(id) on delete cascade,
  event_key text not null, symbol text not null, base_asset text not null, quote_asset text not null,
  baseline_time timestamptz not null, baseline_price numeric not null, crossing_time timestamptz not null,
  crossing_bar_open numeric not null, crossing_bar_high numeric not null, threshold_price numeric not null,
  gain_pct numeric not null, minutes_to_cross integer not null,
  exit_quote_notional numeric not null, exit_trade_count integer not null,
  saleability_source text not null, saleable boolean not null, created_at timestamptz not null default now(),
  unique(scan_job_id,event_key)
);
create index if not exists idx_binance10_events_scan on binance10_events(scan_job_id,crossing_time);
create index if not exists idx_binance10_events_symbol on binance10_events(symbol,baseline_time);

create table if not exists binance10_control_jobs (
  id uuid primary key default gen_random_uuid(), scan_job_id uuid not null references binance10_scan_jobs(id) on delete cascade,
  status text not null check (status in ('queued','running','completed','completed_with_warnings','failed')),
  created_at timestamptz not null default now(), started_at timestamptz, completed_at timestamptz, heartbeat_at timestamptz,
  controls_per_event integer not null default 5 check (controls_per_event between 1 and 10), prior_days integer not null default 10 check (prior_days=10),
  events_processed integer not null default 0, controls_created integer not null default 0, failures integer not null default 0,
  error_message text
);

create table if not exists binance10_controls (
  id uuid primary key default gen_random_uuid(), control_job_id uuid not null references binance10_control_jobs(id) on delete cascade,
  event_id uuid not null references binance10_events(id) on delete cascade, symbol text not null,
  pseudo_baseline_time timestamptz not null, match_rank integer not null, match_score numeric not null,
  match_basis text, same_weekday boolean, calendar_distance_days integer,
  created_at timestamptz not null default now(),
  unique(control_job_id,event_id,pseudo_baseline_time)
);
create index if not exists idx_binance10_controls_job on binance10_controls(control_job_id,event_id);

create table if not exists binance10_context_jobs (
  id uuid primary key default gen_random_uuid(), control_job_id uuid not null references binance10_control_jobs(id) on delete cascade,
  status text not null check (status in ('queued','running','completed','completed_with_warnings','failed')),
  created_at timestamptz not null default now(), started_at timestamptz, completed_at timestamptz, heartbeat_at timestamptz,
  prior_days integer not null default 10 check (prior_days=10), protocol_version text not null,
  events_processed integer not null default 0, samples_total integer not null default 0, feature_rows integer not null default 0,
  raw_bar_rows bigint not null default 0, failures integer not null default 0, result_json jsonb, error_message text
);



-- Safe upgrades for projects initially created with v1.0.x.
alter table binance10_controls add column if not exists match_basis text;
alter table binance10_controls add column if not exists same_weekday boolean;
alter table binance10_controls add column if not exists calendar_distance_days integer;
alter table binance10_context_jobs add column if not exists raw_bar_rows bigint not null default 0;

create table if not exists binance10_files (
  id uuid primary key default gen_random_uuid(), context_job_id uuid not null references binance10_context_jobs(id) on delete cascade,
  storage_path text not null, filename text not null, size_bytes bigint not null, sha256 text not null,
  content_type text not null, role text not null, created_at timestamptz not null default now(),
  unique(context_job_id,storage_path)
);

create table if not exists binance10_issues (
  id bigint generated always as identity primary key,
  scan_job_id uuid references binance10_scan_jobs(id) on delete cascade,
  control_job_id uuid references binance10_control_jobs(id) on delete cascade,
  context_job_id uuid references binance10_context_jobs(id) on delete cascade,
  symbol text, stage text not null, message text not null, created_at timestamptz not null default now()
);

alter table binance10_scan_jobs enable row level security;
alter table binance10_events enable row level security;
alter table binance10_control_jobs enable row level security;
alter table binance10_controls enable row level security;
alter table binance10_context_jobs enable row level security;
alter table binance10_files enable row level security;
alter table binance10_issues enable row level security;

insert into storage.buckets(id,name,public)
values ('binance10-research','binance10-research',false)
on conflict (id) do update set public=false;
-- No anonymous policies are created. The app uses the server-side Supabase secret key only.
