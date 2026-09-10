-- Phase 4: playbook framework — persisted metadata ONLY (§23 of the Phase 4
-- report). Rule logic (prerequisites/triggers/disqualifiers/entry/stop/
-- target/quality-component config) stays code-defined/version-controlled in
-- engine/playbooks/registry.py — this migration never encodes trading logic
-- as data. NEW FILE ONLY — does not alter any Phase 3 (0001-0006) table,
-- column, or policy. Non-destructive, additive, idempotent-safe re-run
-- guarded by `if not exists` / `create or replace` where practical.
--
-- WHY playbook_configs (§20/§22): lets a SUPER_ADMIN enable/disable a
-- playbook or set its ranking priority without a code deploy, while the
-- actual rule logic a disabled playbook would have run remains entirely in
-- version-controlled Python. Readable by any authenticated user (§41);
-- writable only by SUPER_ADMIN, enforced in RLS itself (not just at the
-- Streamlit app layer) since this table's contents affect what every user
-- sees evaluated, unlike the existing Phase 1 "authenticated may write"
-- simplification for purely re-derivable reference market data.
--
-- WHY playbook_config_audit_log (§42): every SUPER_ADMIN change to
-- enabled/priority is audited; ordinary read/evaluation activity is never
-- logged here.
--
-- WHY trade_decisions gets three new nullable columns (§24/§46): existing
-- Phase 3 rows have playbook_id/playbook_version/playbook_family = NULL,
-- which remains valid forever — no backfill, no destructive change. New
-- Phase 4 decisions populate them. See repository/risk_repository.py.

-- ===================== playbook_configs (§20/§22/§23) =====================
create table if not exists playbook_configs (
  playbook_id text primary key,  -- matches engine.playbooks.taxonomy.PlaybookId values exactly
  enabled boolean not null default true,
  priority integer not null default 100,  -- lower = higher ranking precedence (see engine/playbooks/engine.py)
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id)
);

alter table playbook_configs enable row level security;

-- Any authenticated user may READ playbook configuration (§41 — "Global
-- playbook definitions: readable by authenticated users").
drop policy if exists "playbook_configs_select_authenticated" on playbook_configs;
create policy "playbook_configs_select_authenticated" on playbook_configs
  for select using (auth.role() = 'authenticated');

-- Only SUPER_ADMIN may write — checked directly in RLS (not app-layer only),
-- since this table's contents change what every user's Trade Planner
-- evaluates. Mirrors the existing roles/user_roles shape from 0001.
drop policy if exists "playbook_configs_write_super_admin" on playbook_configs;
create policy "playbook_configs_write_super_admin" on playbook_configs
  for all
  using (
    exists (
      select 1 from user_roles ur join roles r on r.id = ur.role_id
      where ur.user_id = auth.uid() and r.name = 'SUPER_ADMIN'
    )
  )
  with check (
    exists (
      select 1 from user_roles ur join roles r on r.id = ur.role_id
      where ur.user_id = auth.uid() and r.name = 'SUPER_ADMIN'
    )
  );

-- ===================== playbook_config_audit_log (§42) =====================
create table if not exists playbook_config_audit_log (
  id bigint generated always as identity primary key,
  playbook_id text not null,
  changed_by uuid references auth.users(id),
  changed_at timestamptz not null default now(),
  field_changed text not null,  -- e.g. 'enabled', 'priority'
  old_value text,
  new_value text
);

alter table playbook_config_audit_log enable row level security;

-- Readable by any authenticated user (transparency); write-only via the
-- same SUPER_ADMIN check — an ordinary user can see the audit trail but
-- never append/alter it themselves.
drop policy if exists "playbook_config_audit_log_select_authenticated" on playbook_config_audit_log;
create policy "playbook_config_audit_log_select_authenticated" on playbook_config_audit_log
  for select using (auth.role() = 'authenticated');

drop policy if exists "playbook_config_audit_log_insert_super_admin" on playbook_config_audit_log;
create policy "playbook_config_audit_log_insert_super_admin" on playbook_config_audit_log
  for insert
  with check (
    exists (
      select 1 from user_roles ur join roles r on r.id = ur.role_id
      where ur.user_id = auth.uid() and r.name = 'SUPER_ADMIN'
    )
  );

-- ===================== trade_decisions: new nullable columns (§24/§46) =====================
alter table trade_decisions add column if not exists playbook_id text;
alter table trade_decisions add column if not exists playbook_version text;
alter table trade_decisions add column if not exists playbook_family text;

-- Seed one row per implementable Phase 4 playbook, all enabled — matches
-- engine/playbooks/registry.py::implementable_definitions() at ship time.
-- Safe to re-run (on_conflict does nothing, never overwrites an admin's
-- prior enabled/priority choice).
insert into playbook_configs (playbook_id, enabled, priority) values
  ('BREAKOUT_RETEST_LONG', true, 100),
  ('BREAKOUT_RETEST_SHORT', true, 100),
  ('TREND_PULLBACK_LONG', true, 100),
  ('TREND_PULLBACK_SHORT', true, 100),
  ('FAILED_BREAKOUT_REVERSAL_LONG', true, 100),
  ('FAILED_BREAKOUT_REVERSAL_SHORT', true, 100),
  ('OPENING_RANGE_BREAKOUT_LONG', true, 100),
  ('OPENING_RANGE_BREAKOUT_SHORT', true, 100),
  ('RANGE_MEAN_REVERSION_LONG', true, 100),
  ('RANGE_MEAN_REVERSION_SHORT', true, 100)
on conflict (playbook_id) do nothing;
