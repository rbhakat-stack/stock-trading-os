# Trading OS — Phase 1 (Market Reader)

Personal trading operating system. Full design/architecture: [TRADING_OS_DESIGN.md](TRADING_OS_DESIGN.md).

Phase 1 delivers: multi-user auth (Supabase Auth + RLS from day one), OHLCV ingestion,
swing detection (major/minor), HH/HL/LH/LL structure labeling, the confirmed-uptrend /
confirmed-downtrend FSM (BOS + pullback confirmation, CHOCH warning states), basic
support/resistance zones, and a Streamlit Market Reader screen with a candlestick
chart + overlays. Everything runs on free tiers (see `TRADING_OS_DESIGN.md` §32a).

## 1. One-time setup (you do this part — account creation isn't something I can do for you)

1. **Supabase** (free): create a project at [supabase.com](https://supabase.com). Then:
   - Open **SQL Editor** in your project and run, in order:
     [`supabase/migrations/0001_phase1_schema.sql`](supabase/migrations/0001_phase1_schema.sql), then
     [`supabase/migrations/0002_admin_rbac.sql`](supabase/migrations/0002_admin_rbac.sql).
   - Go to **Project Settings → API** and copy the **Project URL**, the **anon public** key,
     and — only if you want the Admin Console — the **service_role** key (see step 5 below).
2. **Alpaca** (optional, free): create a paper account at [alpaca.markets](https://alpaca.markets)
   if you want real market data instead of the built-in synthetic demo data. Copy your
   API Key ID and Secret Key from the dashboard.
3. Copy `.env.example` to `.env` and fill in the values from steps 1-2 (leave the two
   `ALPACA_*` values blank to run on synthetic demo data). Leave `APP_URL` as
   `http://localhost:8501` for local development.
4. **Configure Supabase's redirect allowlist** so email verification / password reset
   links actually work — see "Supabase URL Configuration" below. This step is required;
   without it, verification emails will redirect to the wrong place or be rejected.
5. **Admin Console (optional):** to use the Administration section, add
   `SUPABASE_SERVICE_ROLE_KEY` to `.env` — get it from **Project Settings → API →
   service_role** ("secret", not the anon key). **This key must never be shared, committed,
   logged, or exposed to a browser — it bypasses every Row Level Security policy on your
   project.** Leaving it blank is fine; the app runs normally, and admin pages just show
   "Administrative backend is not configured." Then bootstrap your own account as
   SUPER_ADMIN — see "Bootstrapping SUPER_ADMIN" below.

## 2. Supabase URL Configuration (required for email links to work)

In your Supabase project: **Authentication → URL Configuration**.

**Local development:**
- **Site URL:** `http://localhost:8501`
- **Redirect URLs:** `http://localhost:8501/**`

**Production later** (once you have a deployed URL and have set `APP_URL` in production
to match):
- **Site URL:** `https://<your-production-domain>`
- **Redirect URLs:** add both, if you still want to test locally too:
  - `https://<your-production-domain>/**`
  - `http://localhost:8501/**`

The **Site URL** is Supabase's default/fallback redirect target; **Redirect URLs** is the
allowlist of destinations Supabase will actually accept — a redirect target that isn't
covered by this allowlist gets silently rejected by Supabase (the link works but lands
somewhere unexpected). When you deploy to production, update **Site URL** to your
production domain (replacing localhost as the primary), keep localhost in **Redirect
URLs** only if you still want local testing against the same Supabase project, and set
the app's own `APP_URL` environment variable to match — that's the only code-adjacent
change needed; no application code changes between environments.

## 3. Bootstrapping your SUPER_ADMIN account

There is deliberately no signup flag, hard-coded email, or UI button that grants
SUPER_ADMIN — it's a manual, one-time database action so the platform never ends up with
an accidentally-created second owner. After you've created your own account through the
app's normal **Create Account** flow and verified your email:

1. In Supabase, open **Table Editor → user_roles** (or use **SQL Editor**).
2. Find your `user_id` — easiest via **SQL Editor**:
   ```sql
   select id, email from auth.users where email = 'you@example.com';
   ```
3. Update your role row to SUPER_ADMIN (`role_id = 1`):
   ```sql
   update user_roles set role_id = 1 where user_id = '<your-user-id-from-step-2>';
   ```
4. Log out and back in to the app (or just refresh) — the **Administration** section
   appears in the sidebar once your role is SUPER_ADMIN.

## 4. Run it

```bash
python -m venv .venv
.venv/Scripts/activate   # or: source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
streamlit run app/main.py
```

Open the URL Streamlit prints (default `http://localhost:8501`), create an account,
verify your email (Supabase sends this automatically), log in, then go to
**Market Reader**, pick a symbol/timeframe, and click **Refresh Data**.

## 5. Run the tests

The market-structure engine (swing detection, HH/HL/LH/LL, the trend FSM, data-integrity
checks) and the auth/config helpers are pure Python with no external services required:

```bash
pytest tests/ -v
```

## Project layout

```
engine/            deterministic domain logic — no Streamlit or Supabase imports
  data_provider/    market data abstraction (Alpaca + synthetic demo provider)
  data_integrity/   staleness/gap/malformed-bar checks (fail-closed)
  features/         ATR / true range
  market_state/     swing detection, HH/HL/LH/LL, trend FSM, S/R zones
repository/         Supabase access layer (RLS-aware)
  supabase_client.py       anon-key client (RLS-scoped, session-bound)
  supabase_admin_client.py service-role client — server-side only, see its docstring
  admin_repository.py      all privileged admin reads/writes + audit log writes
  feature_flags.py         RLS-readable flag reads (writes go through admin_repository)
app/                Streamlit frontend
  main.py            entry point + navigation gate (Account / Workspace / Administration)
  config.py          centralized env config (APP_URL — see get_app_url())
  auth.py            session-scoped Supabase auth helpers + error classification
  authorization.py   require_authenticated() / require_admin() / require_super_admin()
  pages/             Account, Dashboard, Market Reader, Setup Required, Admin *
supabase/migrations/ SQL schema + RLS policies (0001 Phase 1, 0002 Admin/RBAC)
tests/              pytest suite for the engine + auth/config/authorization helpers
```

## Known simplifications (intentional, documented, revisited in later phases)

- **No Worker process yet** — ingestion runs synchronously from the Streamlit page on
  "Refresh Data," not continuously in the background. The Live Scanner (continuous,
  market-hours-aware refresh) is designed in `TRADING_OS_DESIGN.md` §14a but depends on
  the setup/risk engines that arrive in Phases 2-3.
- **RLS write policies on shared market-data tables are `authenticated`-role, not
  service-role-only** — see the comment block in `0001_phase1_schema.sql`. Safe for a
  single/small-user system today; tighten this once the Worker exists.
- **Gap detection is a WARNING, not calendar-aware** — overnight/weekend gaps are
  expected and not yet distinguished from a real data problem; `trading_calendar_sessions`
  (§14a) will fix this in a later phase.
- **Support/resistance zones are basic proximity clustering** — no retest/role-reversal
  history yet (Phase 2+, §13).
- **Suspension is enforced at the application gate, not at the database/RLS level** —
  a `SUSPENDED` user is blocked from every Streamlit page via `authorization.require_authenticated()`,
  but their existing Supabase session JWT (if still unexpired) is not independently revoked
  and RLS policies on their own data don't check `profiles.status`. This matches what was
  asked for ("prevented from accessing application workspace"), but a fully suspended user
  could in principle still read/write their own RLS-scoped rows via direct API calls until
  their token expires. Closing that would mean adding a status check into every user-owned
  table's RLS policy — a broader change than this phase's scope.
- **Session revocation is genuinely not implemented** (not a placeholder) — the installed
  supabase-py admin API has no supported "revoke this user's sessions by ID" method; see
  the note on the Admin → User Detail page for the specific method that was checked.
- **`list_users` has no server-side search** in the installed client — the Admin Console
  filters client-side after fetching up to 1000 users, which won't scale indefinitely.
