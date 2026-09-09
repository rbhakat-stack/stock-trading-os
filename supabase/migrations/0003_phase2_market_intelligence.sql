-- Phase 2: Market Intelligence Engine.
-- Builds on 0001_phase1_schema.sql and 0002_admin_rbac.sql (do not edit those —
-- they're already applied). Run this once in the Supabase SQL editor.
--
-- PERSISTENCE PHILOSOPHY (see TRADING_OS_DESIGN.md §25 / engine/market_state/
-- market_intelligence.py's docstring): most Phase 2 analysis is computed fresh
-- on every page load from bars already stored (or fetched fresh) — there is
-- still no background Worker to make historical persistence of these values
-- meaningful yet. Only two genuinely historical facts get new tables here:
--
--   1. breakout_events — a breakout's classification EVOLVES as more bars
--      arrive (e.g. "still developing" -> SUCCESSFUL_BREAKOUT a few bars
--      later), so a single current value can't represent its history. Matches
--      the append-only evidence pattern swing_points/market_state_events
--      already use.
--   2. opening_range_events — one row per symbol/timeframe/trading day; cheap,
--      and useful for Phase 3 backtesting later.
--
-- Everything else (trend quality, consolidation/compression, volume/RVOL,
-- ATR/volatility regime, multi-timeframe alignment) is NOT given a table —
-- it's recomputed on every request, same as Phase 1 already does for
-- swings/trend. BOS/CHOCH strength reuses the EXISTING market_state_events
-- .evidence jsonb column (already flexible) rather than adding columns.

-- ===================== S/R zone enrichment (§8) =====================
alter table sr_zones add column rejection_count int not null default 0;
alter table sr_zones add column break_count int not null default 0;
alter table sr_zones add column retest_count int not null default 0;
alter table sr_zones add column last_touch_time timestamptz;
alter table sr_zones add column role_reversal_history jsonb not null default '[]';

-- ===================== Breakout events (§9-12) =====================
create table breakout_events (
  id bigint generated always as identity primary key,
  symbol text not null references symbols(symbol),
  timeframe bar_timeframe not null,
  ts timestamptz not null,
  source text not null check (source in ('BOS', 'SR_ZONE', 'OPENING_RANGE')),
  level numeric not null,
  direction text not null check (direction in ('UP', 'DOWN')),
  state text not null,  -- a BreakoutState value, e.g. 'SUCCESSFUL_BREAKOUT_RETEST'
  retest_state text,    -- a RetestState value
  follow_through_state text,  -- a FollowThroughState value
  evidence jsonb not null default '{}',
  algorithm_version text not null,
  created_at timestamptz not null default now()
);
create index breakout_events_symbol_tf_idx on breakout_events (symbol, timeframe, ts desc);

-- ===================== Opening range events (§17) =====================
create table opening_range_events (
  id bigint generated always as identity primary key,
  symbol text not null references symbols(symbol),
  timeframe bar_timeframe not null,
  session_date date not null,
  window_minutes int not null check (window_minutes in (5, 15, 30, 60)),
  orh numeric,
  orl numeric,
  midpoint numeric,
  width numeric,
  width_atr numeric,
  opening_volume bigint,
  status text not null,  -- INSIDE_OPENING_RANGE / ABOVE_ORH / BELOW_ORL / a BreakoutState value / INSUFFICIENT_DATA
  evidence jsonb not null default '{}',
  algorithm_version text not null,
  created_at timestamptz not null default now(),
  unique (symbol, timeframe, session_date, window_minutes)
);
create index opening_range_events_symbol_tf_idx on opening_range_events (symbol, timeframe, session_date desc);

-- ===================== RLS =====================
alter table breakout_events enable row level security;
alter table opening_range_events enable row level security;

create policy "breakout_events_select_authenticated" on breakout_events for select using (auth.role() = 'authenticated');
create policy "opening_range_events_select_authenticated" on opening_range_events for select using (auth.role() = 'authenticated');

-- PHASE 1/2 SIMPLIFICATION (intentional, temporary — same as 0001's market-data
-- write policies): there is still no always-on Worker, so any authenticated
-- user may write these tables directly from the Streamlit app. Safe for a
-- single/small-user system because the worst case is a logged-in user
-- polluting shared, re-derivable market data — never another user's
-- portfolio/risk/financial data, which stay owner-scoped. Tighten to
-- service-role-only once the Worker exists.
create policy "breakout_events_write_authenticated" on breakout_events for insert with check (auth.role() = 'authenticated');
create policy "opening_range_events_write_authenticated" on opening_range_events for all
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
