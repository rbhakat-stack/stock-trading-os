-- Phase 3 exposure/leverage hardening. Builds on 0001-0004 (do not edit
-- those — already applied). Run once in the Supabase SQL editor.
--
-- Adds only the two raw notional totals needed to model gross exposure, net
-- exposure, and leverage correctly (see engine/risk/account_state.py):
-- gross/net/leverage are all pure derivations of these two numbers, so only
-- the two are persisted — nothing else needs its own column, and no new
-- table is created (this is settings data, same reasoning as 0004's
-- account_risk_states already applies).
--
-- `not null default 0` backfills any existing row (a flat account with no
-- prior exposure entered) without requiring the user to re-save first.

alter table account_risk_states
  add column long_exposure_notional numeric not null default 0,
  add column short_exposure_notional numeric not null default 0;

-- No RLS changes needed: account_risk_states' existing owner-scoped policy
-- ("account_risk_states_all_own", from 0004) already covers every column on
-- the table, these two included — RLS in Postgres is row-scoped, not
-- column-scoped, so a new column on an already-RLS-enabled table inherits
-- the same access rule automatically.
