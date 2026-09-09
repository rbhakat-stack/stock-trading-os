-- Phase 3 final hardening: per-symbol position model + the daily
-- realized-loss policy control. Builds on 0001-0005 (do not edit those —
-- already applied). Run once in the Supabase SQL editor.
--
-- WHY account_positions (§2 of the final hardening audit): existing_symbol_
-- exposure was previously a caller-supplied stub, always 0 in the live app,
-- because nothing tracked per-symbol notional. This table is the owner-scoped
-- authoritative source for a user's current same-symbol position when they've
-- entered one — see engine/risk/positions.py for the signed-quantity model
-- and how gross/net/leverage/open_positions/open_risk are DERIVED from it
-- rather than duplicated. Manual/simulated only, same as every other Phase 3
-- account table — a future broker-integration phase would populate this from
-- live positions instead, without changing its shape.
--
-- WHY max_daily_realized_loss_pct on risk_policies (§9): a SEPARATE control
-- from the existing max_daily_loss_pct (equity drawdown) — realized trading
-- losses can be masked by unrealized gains in the equity figure alone.

-- ===================== risk_policies: new column =====================
alter table risk_policies
  add column max_daily_realized_loss_pct numeric not null default 2.0;

-- ===================== account_positions (§2) =====================
create table account_positions (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  symbol text not null references symbols(symbol),
  quantity_signed numeric not null,  -- + = LONG, - = SHORT; a row with 0 should be deleted, not kept
  reference_price numeric not null check (reference_price > 0),
  average_price numeric,
  planned_stop_price numeric,
  updated_at timestamptz not null default now(),
  unique (user_id, symbol)
);
create index account_positions_user_idx on account_positions (user_id);

-- ===================== RLS (§23) =====================
alter table account_positions enable row level security;

-- Full CRUD, owner-scoped only — identical shape to risk_policies /
-- account_risk_states from 0004. No broader "authenticated" policy exists on
-- this table, so no other user's positions are reachable through any policy
-- here; admin access, if ever needed, goes through the existing service-role
-- admin path only.
create policy "account_positions_all_own" on account_positions for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());
