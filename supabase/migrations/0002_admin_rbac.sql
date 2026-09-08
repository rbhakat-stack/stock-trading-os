-- Admin Console + RBAC enforcement.
-- Builds on 0001_phase1_schema.sql (do not edit that file — it's already applied).
-- Run this once in the Supabase SQL editor (or via `supabase db push`).

-- ===================== Application-level account status (§15) =====================
create type account_status as enum ('ACTIVE', 'SUSPENDED');

alter table profiles add column status account_status not null default 'ACTIVE';

-- SECURITY: without this, the existing "profiles_update_own" policy (id = auth.uid(),
-- no column restriction) would let a suspended user simply PATCH their own status back
-- to ACTIVE — a self-reactivation privilege escalation. This column-level REVOKE closes
-- that path while leaving the rest of the profile (display_name, timezone, base_currency)
-- editable by its owner as before. Status changes happen only through admin_repository.py
-- using the service-role client, which is not subject to table/column GRANTs.
revoke update (status) on profiles from authenticated;

-- ===================== Admin audit log (§17-19) =====================
create table admin_audit_events (
  id bigint generated always as identity primary key,
  admin_user_id uuid not null references auth.users(id),
  target_user_id uuid references auth.users(id),
  action text not null,
  resource_type text,
  resource_id text,
  before_state jsonb,
  after_state jsonb,
  reason text,
  request_metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index admin_audit_events_created_at_idx on admin_audit_events (created_at desc);
create index admin_audit_events_target_user_idx on admin_audit_events (target_user_id);

alter table admin_audit_events enable row level security;
-- Deliberately NO policies at all: RLS defaults to deny for every request that isn't
-- using the service-role key (which bypasses RLS entirely). Reads and writes both go
-- exclusively through repository/admin_repository.py's service-role client, gated by
-- authorization.require_super_admin() in Python before any call reaches this table.
-- This is stricter than "admins can read via RLS" — it removes the client-side read
-- path altogether, so a bug in a future RLS policy here can't leak audit data.

-- ===================== Feature flags (§21/§37) =====================
create table feature_flags (
  key text primary key,
  enabled boolean not null default false,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id)
);

insert into feature_flags (key, enabled) values
  ('paper_trading_enabled', true),
  ('live_decision_support_enabled', false),
  ('live_execution_enabled', false),  -- hard-locked in application code regardless of this value; see app/pages/admin_system_controls.py
  ('maintenance_mode', false);

alter table feature_flags enable row level security;
-- Readable by any authenticated user (same pattern as the `roles` lookup table in
-- 0001) since flag state gates ordinary-page behavior (e.g. maintenance mode) and
-- isn't sensitive. Writes go through the service-role admin client only.
create policy "feature_flags_select_authenticated" on feature_flags
  for select using (auth.role() = 'authenticated');
