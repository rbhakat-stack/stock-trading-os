-- Application acceptance hardening: fixes "Unable to persist market
-- analysis" in Market Reader.
--
-- ROOT CAUSE: repository/market_data.py's upsert_bars() and
-- upsert_swing_points() call Supabase's `.upsert(..., on_conflict=...)`,
-- which PostgREST/Postgres implements as
--   INSERT ... ON CONFLICT (...) DO UPDATE SET ...
-- The UPDATE branch requires UPDATE privilege on the table, evaluated by
-- its own RLS policy — separate from the INSERT policy. 0001_phase1_schema
-- only granted `bars_write_authenticated`/`swing_points_write_authenticated`
-- as `for insert` (see that file) — there was never a matching `for update`
-- policy. The FIRST refresh of a symbol/timeframe/date range (every row a
-- genuine new INSERT) succeeds; any SUBSEQUENT refresh whose date range
-- overlaps previously-stored bars/swing points (a normal, expected thing to
-- do) hits the ON CONFLICT DO UPDATE path for those rows, which RLS then
-- silently blocks — surfacing to the user as the generic "Unable to persist
-- market analysis" warning.
--
-- `symbols` already got this right (both `symbols_write_authenticated` FOR
-- INSERT and `symbols_update_authenticated` FOR UPDATE exist) — this
-- migration brings `bars` and `swing_points` up to that same, already-
-- established pattern. Same security model as every other policy on these
-- two "shared, re-derivable market data" tables (see 0001's own comment):
-- any authenticated user may write, never expands access beyond what
-- INSERT already allowed for these specific rows.

create policy "bars_update_authenticated" on bars for update
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');

create policy "swing_points_update_authenticated" on swing_points for update
  using (auth.role() = 'authenticated') with check (auth.role() = 'authenticated');
