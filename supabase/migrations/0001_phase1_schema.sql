-- Phase 1 schema: auth/profile scaffolding, shared reference market data,
-- swing detection, market structure, and user watchlists.
-- Run this once in the Supabase SQL editor (or via `supabase db push`) on a fresh project.

-- ===================== Enums =====================
create type app_role as enum ('SUPER_ADMIN', 'ADMIN', 'USER');
create type asset_type as enum ('EQUITY', 'ETF', 'OPTION', 'FUTURE', 'CRYPTO');
create type bar_timeframe as enum ('1min','5min','15min','30min','1hour','1day','1week');
create type swing_type as enum ('HIGH','LOW');
create type swing_significance as enum ('MAJOR','MINOR');
create type structure_label as enum ('HH','HL','LH','LL','NONE');
create type market_state as enum (
  'RANGE','TRANSITIONAL_BULLISH','TRANSITIONAL_BEARISH',
  'UPTREND_CONFIRMED','DOWNTREND_CONFIRMED','UPTREND_WARNING','DOWNTREND_WARNING'
);
create type zone_type as enum ('SUPPORT','RESISTANCE');

-- ===================== Profiles (mirrors auth.users) =====================
create table profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  timezone text not null default 'America/New_York',
  base_currency text not null default 'USD',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ===================== Roles / RBAC (groundwork for later phases) =====================
create table roles (
  id smallint primary key,
  name app_role not null unique
);
insert into roles (id, name) values (1,'SUPER_ADMIN'), (2,'ADMIN'), (3,'USER');

alter table roles enable row level security;
-- Static lookup data, not sensitive — any authenticated user may read role names.
-- No write policy: only service_role can modify this table (there are only ever 3 rows).
create policy "roles_select_authenticated" on roles for select using (auth.role() = 'authenticated');

create table user_roles (
  user_id uuid not null references auth.users(id) on delete cascade,
  role_id smallint not null references roles(id),
  granted_at timestamptz not null default now(),
  granted_by uuid references auth.users(id),
  primary key (user_id, role_id)
);

-- Auto-provision a profile + USER role on signup.
create function public.handle_new_user() returns trigger as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, new.raw_user_meta_data->>'display_name');
  insert into public.user_roles (user_id, role_id) values (new.id, 3);
  return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- ===================== Shared reference market data (not tenant-owned) =====================
create table symbols (
  symbol text primary key,
  name text,
  exchange text,
  asset_type asset_type not null default 'EQUITY',
  created_at timestamptz not null default now()
);

create table bars (
  id bigint generated always as identity primary key,
  symbol text not null references symbols(symbol),
  timeframe bar_timeframe not null,
  ts timestamptz not null,
  open numeric not null,
  high numeric not null,
  low numeric not null,
  close numeric not null,
  volume bigint not null,
  provider text not null,
  created_at timestamptz not null default now(),
  unique (symbol, timeframe, ts)
);
create index bars_symbol_tf_ts_idx on bars (symbol, timeframe, ts desc);

create table swing_points (
  id bigint generated always as identity primary key,
  symbol text not null references symbols(symbol),
  timeframe bar_timeframe not null,
  ts timestamptz not null,
  price numeric not null,
  swing_type swing_type not null,
  significance swing_significance not null,
  score numeric not null,
  algorithm_version text not null,
  evidence jsonb not null default '{}',
  created_at timestamptz not null default now(),
  unique (symbol, timeframe, ts, swing_type)
);
create index swing_points_symbol_tf_idx on swing_points (symbol, timeframe, ts desc);

-- Human overrides of an algorithmic swing classification; the original algorithmic
-- output in swing_points is never mutated — this is an additive, auditable layer.
create table swing_overrides (
  id bigint generated always as identity primary key,
  swing_point_id bigint not null references swing_points(id) on delete cascade,
  user_id uuid not null references auth.users(id),
  overridden_type swing_type,
  overridden_significance swing_significance,
  reason text,
  created_at timestamptz not null default now()
);

create table market_state_events (
  id bigint generated always as identity primary key,
  symbol text not null references symbols(symbol),
  timeframe bar_timeframe not null,
  ts timestamptz not null,
  state market_state not null,
  structure_label structure_label not null default 'NONE',
  evidence jsonb not null default '{}',
  algorithm_version text not null,
  created_at timestamptz not null default now()
);
create index market_state_events_symbol_tf_idx on market_state_events (symbol, timeframe, ts desc);

create table sr_zones (
  id bigint generated always as identity primary key,
  symbol text not null references symbols(symbol),
  timeframe bar_timeframe not null,
  zone_type zone_type not null,
  upper_boundary numeric not null,
  lower_boundary numeric not null,
  strength_score numeric not null default 0,
  touch_count int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index sr_zones_symbol_tf_idx on sr_zones (symbol, timeframe);

-- ===================== User-owned: watchlists =====================
create table watchlists (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  created_at timestamptz not null default now()
);

create table watchlist_items (
  id bigint generated always as identity primary key,
  watchlist_id bigint not null references watchlists(id) on delete cascade,
  symbol text not null references symbols(symbol),
  added_at timestamptz not null default now(),
  unique (watchlist_id, symbol)
);

-- ===================== Row Level Security =====================
alter table profiles enable row level security;
alter table user_roles enable row level security;
alter table watchlists enable row level security;
alter table watchlist_items enable row level security;
alter table swing_overrides enable row level security;
alter table symbols enable row level security;
alter table bars enable row level security;
alter table swing_points enable row level security;
alter table market_state_events enable row level security;
alter table sr_zones enable row level security;

-- profiles: user manages own row
create policy "profiles_select_own" on profiles for select using (id = auth.uid());
create policy "profiles_update_own" on profiles for update using (id = auth.uid());
create policy "profiles_insert_own" on profiles for insert with check (id = auth.uid());

-- user_roles: user can read own role assignments; writes are service-role only (no policy = no client write access)
create policy "user_roles_select_own" on user_roles for select using (user_id = auth.uid());

-- watchlists / items: full CRUD scoped to the owning user
create policy "watchlists_all_own" on watchlists for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "watchlist_items_all_own" on watchlist_items for all
  using (watchlist_id in (select id from watchlists where user_id = auth.uid()))
  with check (watchlist_id in (select id from watchlists where user_id = auth.uid()));

-- swing_overrides: user can read/insert their own overrides
create policy "swing_overrides_select_own" on swing_overrides for select using (user_id = auth.uid());
create policy "swing_overrides_insert_own" on swing_overrides for insert with check (user_id = auth.uid());

-- Shared reference/market data: any authenticated user may read.
create policy "symbols_select_authenticated" on symbols for select using (auth.role() = 'authenticated');
create policy "bars_select_authenticated" on bars for select using (auth.role() = 'authenticated');
create policy "swing_points_select_authenticated" on swing_points for select using (auth.role() = 'authenticated');
create policy "market_state_events_select_authenticated" on market_state_events for select using (auth.role() = 'authenticated');
create policy "sr_zones_select_authenticated" on sr_zones for select using (auth.role() = 'authenticated');

-- PHASE 1 SIMPLIFICATION (intentional, temporary — see TRADING_OS_DESIGN.md §21):
-- There is no always-on Worker yet, so Phase 1 lets any authenticated user write
-- reference market data directly from the Streamlit app. This is acceptable for a
-- single/small-user personal system because the worst case is a logged-in user
-- polluting *shared, re-derivable* market data — never another user's portfolio,
-- risk, or financial data, which stay owner-scoped above. Before Phase 2's Worker
-- takes over ingestion (service-role key only), replace these four policies with
-- service-role-only writes, or route writes through a security-definer RPC.
create policy "symbols_write_authenticated" on symbols for insert with check (auth.role() = 'authenticated');
create policy "symbols_update_authenticated" on symbols for update using (auth.role() = 'authenticated');
create policy "bars_write_authenticated" on bars for insert with check (auth.role() = 'authenticated');
create policy "swing_points_write_authenticated" on swing_points for insert with check (auth.role() = 'authenticated');
create policy "market_state_events_write_authenticated" on market_state_events for insert with check (auth.role() = 'authenticated');
create policy "sr_zones_write_authenticated" on sr_zones for all
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
