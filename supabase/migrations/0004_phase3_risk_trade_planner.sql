-- Phase 3: Risk Engine + Trade Planner + NO-TRADE Engine.
-- Builds on 0001-0003 (do not edit those — already applied). Run once in the
-- Supabase SQL editor.
--
-- PERSISTENCE PHILOSOPHY (§27): three tables, deliberately not more.
--
--   1. risk_policies — genuine per-user SETTINGS (one row per user, upserted
--      whenever the user edits their risk policy in the UI). Needs
--      persistence because it's user-entered configuration, not derived.
--   2. account_risk_states — genuine per-user manual/simulated account entry
--      (one row per user, upserted). Same reasoning: it's data the user
--      typed in, not something recomputed from market data.
--   3. trade_decisions — an APPEND-ONLY audit log of computed trade-planning
--      OUTCOMES (one row per "Analyze" click), including NO-TRADE outcomes.
--      This is deliberately NOT a full normalized trade_candidates/trade_plans
--      schema: the full nested plan (reasons, evidence, target detail) lives
--      in one `evidence` jsonb column. Fully normalizing that structure now
--      would be premature — there's no Phase 5 backtesting yet to consume a
--      richer schema, and the plan is entirely recomputed on every request
--      anyway (matching how MarketIntelligenceSnapshot itself isn't
--      persisted as a whole object in Phase 2).

-- ===================== risk_policies (§11, §26) =====================
create table risk_policies (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  risk_per_trade_pct numeric not null,
  max_daily_loss_pct numeric not null,
  max_weekly_loss_pct numeric not null,
  max_portfolio_heat_pct numeric not null,
  max_symbol_exposure_pct numeric not null,
  max_gross_exposure_pct numeric not null,
  max_net_exposure_pct numeric not null,
  max_open_positions int not null,
  max_trades_per_day int not null,
  min_rr numeric not null,
  max_leverage numeric not null,
  cooldown_after_losses int not null,
  max_consecutive_losses int not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id)
);

-- ===================== account_risk_states (§13, §25) =====================
create table account_risk_states (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  source text not null check (source in ('MANUAL', 'PAPER', 'BROKER')),
  net_liquidation_value numeric not null,
  cash numeric not null,
  buying_power numeric not null,
  realized_pnl_today numeric not null default 0,
  unrealized_pnl numeric not null default 0,
  daily_start_equity numeric not null,
  weekly_start_equity numeric not null,
  open_risk numeric not null default 0,
  open_positions int not null default 0,
  trades_today int not null default 0,
  consecutive_losses int not null default 0,
  updated_at timestamptz not null default now(),
  unique (user_id)
);

-- ===================== trade_decisions (§23, §27) =====================
create table trade_decisions (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  symbol text not null references symbols(symbol),
  timeframe bar_timeframe not null,
  setup_type text,
  direction text not null check (direction in ('LONG', 'SHORT', 'NONE')),
  decision text not null check (decision in ('REJECT', 'WAIT', 'CONDITIONAL', 'QUALIFIED')),
  quality_score int,
  quality_band text,
  entry_zone_low numeric,
  entry_zone_high numeric,
  entry_price numeric,
  stop numeric,
  target_1 numeric,
  target_2 numeric,
  rr1 numeric,
  rr2 numeric,
  position_size int,
  position_value numeric,
  capital_at_risk numeric,
  portfolio_heat_before numeric,
  portfolio_heat_after numeric,
  no_trade_reasons jsonb not null default '[]',
  reasons_for jsonb not null default '[]',
  reasons_against jsonb not null default '[]',
  evidence jsonb not null default '{}',
  as_of timestamptz,
  created_at timestamptz not null default now()
);
create index trade_decisions_user_symbol_idx on trade_decisions (user_id, symbol, created_at desc);

-- ===================== RLS (§28) =====================
alter table risk_policies enable row level security;
alter table account_risk_states enable row level security;
alter table trade_decisions enable row level security;

-- risk_policies / account_risk_states: full CRUD, owner-scoped only — mirrors
-- the existing watchlists pattern from 0001 exactly.
create policy "risk_policies_all_own" on risk_policies for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "account_risk_states_all_own" on account_risk_states for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- trade_decisions: append-only from the client's perspective — select + insert
-- only, no update/delete policy, so a user's own decision log can't be edited
-- after the fact (mirrors admin_audit_events' append-only spirit from 0002,
-- but here it's the user's OWN data, not a super-admin-only table, so normal
-- owner-scoped select+insert is correct rather than a total client lockout).
create policy "trade_decisions_select_own" on trade_decisions for select using (user_id = auth.uid());
create policy "trade_decisions_insert_own" on trade_decisions for insert with check (user_id = auth.uid());

-- No other user's risk policy, account state, or trade decisions are reachable
-- through any policy above — every policy is scoped to auth.uid() = user_id,
-- with no broader "authenticated" policy on any of these three tables. Admin
-- access, if ever needed, goes through the existing service-role admin path
-- (repository/supabase_admin_client.py) only — nothing new is added here.
