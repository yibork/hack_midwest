# AGENTS.md: handoff for coding agents

Read this file first, then `docs/architecture.md` (the pipeline, agents, caching, rate limits and
scheduler in detail) and `docs/sources.md` (source research and the local-signal allowlist).

## What this is

**Closure Call** is a Hack Midwest 2026 entry for three challenges: **USDA** (facility operating
decisions, targeted notifications, a decision record), **AWS Strands Agents SDK** (agents that
gather evidence and answer the manager's questions) and **Akamai Cloud** (Linode hosting plus the
Akamai inference service). A facility manager at 4 a.m. must choose: open normally, delay, dismiss
early, telework or close. Closure Call puts the evidence together, recommends a posture, and
notifies the right people once the manager approves.

**Core principle: agents gather evidence and write; deterministic rules decide; humans approve.**
- Agents never choose the posture, the recipients, the times or the access rights.
- `rules/engine.py` is pure code over `rules/thresholds.yaml`. The most restrictive posture wins,
  and local signals can raise the posture but never lower it.
- No operational message goes out without a stored manager decision (`DispatchGuard` cancels any
  `send_*` tool call that has none).
- Missing data is `unknown`, never assumed fine. News alone never triggers a closure.

**Demo story:** live NWS/river/road evidence → explained recommendation (triggers, confidence,
what would change it) → the manager asks a follow-up question → approve or override with a reason
→ code re-checks shifts, leave and essential roles → targeted notices (push/email) with ack and
escalation → an append-only decision record that can be replayed. Demo facility: `usda-kc-demo`
(USDA Kansas City Field Office, synthetic roster in `backend/app/seed.py`). Roles are switched
with the header switcher (`X-Role` / `X-Person`, no real auth; see `app/roles.py`).

## Architecture map

```
backend/app/
  main.py            FastAPI app, background loops (escalation, scheduler, roads watcher), SPA serving
  api.py             manager API: evaluate, recommendations, /map, /snapshots/{sha256}, decisions, notify, record
  people_api.py      team, /me, notices, acks, access lists, /demo/simulate (DEMO_MODE only)
  pipeline.py        evaluate_facility(): one evaluation under one evaluation_id
  rules/engine.py    pure rules engine + confidence; rules/thresholds.yaml = policy (version string)
  rules/local.py     local-signal rules (roads/schools/public safety/news)
  agents/            Strands agents on Akamai inference
    model.py, runtime.py   GuardedOpenAIModel, agent construction, timeouts
    ratelimit.py           process-wide sliding window + per-evaluation budgets
    hooks.py               AuditHook (every model/tool call recorded), DispatchGuard
    weather_agent.py       NWS tools + relevance notes
    writer_agent.py        rationale paragraph (async, validated)
    message_agent.py       per-recipient-group message templates
    local/                 coordinator, prefilter, specialists (roads/schools/public_safety/news),
                           facts.py (LocalFacts validation), roads_cache.py, run.py
  sources/           nws.py (NWS client + cache), local_signals.py (allowlisted fetchers),
                     geocode.py (Nominatim), registry.yaml (every source + authority)
  notify/            the "people engine": exposure, impact, plan, instructions, messages,
                     notices, channels (ntfy/Resend/console), delivery, escalation
  staffing/coverage.py   minimum on-site coverage per critical function and posture
  access/policy.py(+yaml) facility access list per posture
  calendar.py        operating calendar: Mon-Fri minus US federal holidays
  geo.py, mapdata.py location relevance; map payload with corridor privacy
  scheduler.py       re-evaluations 15:00 day before and 04:00 on the day
  record.py          append-only RecordEvent writer (redacts secrets); replay via /api/record/at
  db.py, models.py, schemas.py   SQLAlchemy models, additive auto-migration, Pydantic schemas
backend/tests/       pytest suite (227 tests at 74c242e); conftest.py blocks live inference
frontend/src/        React 18 + Vite + Tailwind 4 + Leaflet; pages/ (Decide, Facility, Team,
                     MyStatus, Record, Sources, National, Personas, Security), components/
scripts/             deploy.sh (on the VM), server_setup.sh (one-time VM setup)
compose.prod.yml, Caddyfile, Dockerfile   production stack; docker-compose.yml = local with Postgres
```

Caching (details in docs/architecture.md): NWS responses 2 min in memory; local source fetches per
source `refresh_minutes`; evidence reused within an evaluation by snapshot hash; road facts in the
`road_facts_cache` table keyed by facility + target day + geometry + content hash of parsed items;
message drafts by hash of a group's facts. Raw source responses are saved to `SNAPSHOT_DIR` as
`<sha256>.json` and served as plain text by `/api/snapshots/{sha256}`.

## How to run

```bash
# backend (Python 3.11+; a venv exists at backend/.venv locally)
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000        # reads repo-root .env
python -m pytest -q                              # all tests; no network to inference
python scripts/smoke_llm.py                      # LIVE inference check: uses team quota, run sparingly

# frontend (Node 20)
cd frontend && npm ci
npm run dev        # Vite, proxies /api to http://localhost:8000 (override: VITE_API_PROXY)
npm test           # vitest
npm run build      # tsc -b && vite build -> frontend/dist (served by FastAPI in prod)

# full stack locally with Postgres
docker compose up --build
```

Env var **names** (see `.env.example` and `backend/app/config.py`; never print values):
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `CHAT_MODEL`, `EMBEDDING_MODEL` (fallback names `BASE_URL`,
`API_KEY`), `DATABASE_URL` (empty = SQLite; `SQLITE_PATH`), `NWS_USER_AGENT`,
`LLM_REQUESTS_PER_MINUTE` (45), `LLM_CALLS_PER_EVALUATION` (12), `AGENT_TIMEOUT_SECONDS`,
`LOCAL_AGENT_TIMEOUT_SECONDS`, `NTFY_TOPIC`, `RESEND_API_KEY`, `RESEND_FROM`,
`NOTIFY_EMAIL_OVERRIDE`, `SNAPSHOT_DIR`, `SITE_DOMAIN`, `PUBLIC_BASE_URL`, `DEMO_MODE`,
`FRONTEND_DIST`, `OBJ_*` (unused). Feature switches (default on, tests turn off): `LOCAL_SIGNALS`,
`SCHEDULER_LOOP`, `ROADS_WATCH`, `ESCALATION_LOOP`, `PREDRAFT`, `GEOCODE`, `RESOLVE_HOME_ZONES`.
`CLOSURECALL_DOTENV=0` stops config.py from reading `.env` (tests, containers). Values in `.env`
beat the shell environment on purpose.

**DEMO_MODE=1**: ack reminder/escalation timers shrink to 1/2/3 minutes (escalation loop every
15 s); `/api/demo/simulate` can inject a home-zone warning or a road closure, stored with source
`demo` and labelled "SIMULATED for the demo"; the manager can evaluate "today as a workday"
(`demo_workday`). Without DEMO_MODE those return 403.

## Deploy

- **Production:** https://closurecall.duckdns.org (Akamai Linode VM, Docker Compose: `app` +
  `caddy` with automatic HTTPS for `SITE_DOMAIN`). DB: `DATABASE_URL` (Akamai Managed PostgreSQL)
  or SQLite in the `dbdata` volume (`closure_call_v2.db`). Snapshots in the `snapshots` volume.
- **Flow:** push to `main` → `.github/workflows/ci.yml` (pytest, vitest, build) → on success
  `deploy.yml` SSHes to the VM and runs `~/closure-call/scripts/deploy.sh`. Repo secrets:
  `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`; optional variable `PUBLIC_URL`.
- **`AUTO_DEPLOY`** repo variable: set to `false` to stop automatic deploys after CI. Manual
  deploy is always possible: Actions → Deploy → Run workflow (`gh workflow run Deploy`).
- **deploy.sh** fetches `origin/main`, `git reset --hard`, `docker compose -f compose.prod.yml up
  -d --build`, waits up to 120 s for `/api/health` with `"inference": "configured"`, and rolls
  back to the previous commit automatically if unhealthy. Log: `~/deploys.log` on the VM.
- **Manual on the VM** (as the deploy user):
  ```bash
  ~/closure-call/scripts/deploy.sh                       # deploy origin/main
  cd ~/closure-call && git reset --hard <good_sha> && \
    docker compose -f compose.prod.yml up -d --build     # manual rollback (next push redeploys main)
  docker compose -f compose.prod.yml logs --tail 100 app
  ```
  For a lasting rollback, `git revert` on main and push, or set `AUTO_DEPLOY=false` first.
- The server `.env` lives at `~/closure-call/.env` (chmod 600), copied by scp; it is not in the
  image.

## Guardrails (non-negotiable)

1. **Secrets:** never print, log, echo or commit `.env` contents or keys (`hk_...`, Resend,
   deploy SSH key). Use `config.redact()` on any text that might contain one. Show env var
   names only. (Note: `.env` is currently tracked in git; see Known issues.)
2. **Tests never call the live Akamai inference endpoint.** `tests/conftest.py` fails any test
   that resolves `*.akamai-workshops.com`; mock the model. Keep it that way.
3. **Quota:** the team shares 60 requests/min across all endpoints. The process limiter is 45/min;
   each evaluation has a 12-call budget (coordinator reserves 2 for the writer); notices,
   pre-drafts and background roads runs use their own budget keys. Do not add model calls without
   a budget key and a deterministic fallback. Scheduled re-checks make no model calls.
4. **Schema changes are additive only.** Never drop or rename tables or columns, never delete
   rows: recorded evaluations are demo insurance. `db.py` auto-adds new *nullable* columns; a new
   NOT NULL column raises on purpose. `record.py` is append-only.
5. **Simulated data is labelled SIMULATED**, everywhere it appears (UI, record, messages). Live,
   recorded and simulated evidence must stay distinguishable.
6. **Ask the user before deleting anything on the server** (volumes, DB files, snapshots, images
   beyond the automatic prune, the `.env`).
7. Source text fetched by agents is untrusted input; only allowlisted sources in
   `sources/registry.yaml`; snapshots are served as plain text, never rendered.
8. No API response may contain employee home coordinates (a test checks every role).

## Status

Done (newest first):
- `74c242e` location provenance for every local fact; Leaflet map (`/api/recommendations/{id}/map`)
  with saved-copy links and privacy-aggregated commute corridors
- `464b831` people summary shows minor road conditions in commute corridors; docs/architecture.md
- `c374dc9` road facts cache, background road watcher, preliminary recommendations (409 until final)
- `d30f8ca` writer async; 140-word hard limit with exact-cut retry; decisive confidence reason first
- `5cff33f` writer validation: threshold references don't count as recommending another posture
- `3dd53b0` missing hourly data = unknown, lead-time confidence caps, scheduled re-evaluations
- `3b42a4a` location-aware road relevance, operating calendar
- `ceb8e73` local signals layer: roads, schools, public safety, news agents
- `556ccf6` production deployment (Caddy + compose) and CI/CD to the Linode

Pending, in priority order:
1. **Threshold citations → policy 1.0.0.** `rules/thresholds.yaml` is `0.1.0-draft` and every
   `citation` is `TODO: verify`. Replace with real NWS/agency references (human confirms), bump to
   `1.0.0`. The policy version includes a content hash and is stored on each recommendation.
2. **Investigation panel.** Manager asks a follow-up ("Is the access road still closed?"); a
   Strands agent investigates approved sources or explains saved evidence; new findings produce a
   revised recommendation via the rules engine (never the agent). Needs its own budget key.
3. **Evidence "Saved copy" links with hash verify.** `/api/snapshots/{sha256}` exists; add
   saved-copy links on every evidence item and a verify step that recomputes the SHA-256 and
   shows match/mismatch.
4. **Map with location provenance.** Largely done in `74c242e`; check the remaining gaps (map on
   every relevant page, legend/provenance wording) rather than rebuilding.
5. **River gauge KCDM7.** Registered in `sources/registry.yaml` (`nwps_kcdm7`), and `nwps` is an
   allowed Evidence source, but **no client exists**. Add a fetcher for
   `https://api.water.noaa.gov/nwps/v1/gauges/KCDM7` (+ stageflow forecast), store Evidence, and
   rules against its official flood categories.
6. **Decision memo + tamper-evident record.** Printable memo per decision; hash-chain RecordEvents
   (each event stores the previous event's hash) with a verify endpoint. Additive columns only.
7. **Demo reset + scenario switch.** Reset demo state without deleting recorded data (e.g. a new
   demo epoch/scenario id) and switch between live / recorded / simulated scenarios, clearly
   labelled.

## Known quirks and decisions

- **Road cache keys on content, not raw bytes.** MoDOT regenerates its feed every minute with new
  record IDs and KC Scout includes live travel-time signs, so the hash covers parsed, area-filtered
  items (id, title, text, coords, start time) and item ids are content-based.
- **Preliminary recommendations:** on a road-cache miss the recommendation is preliminary and
  decisions return 409 until the background run finalises it in place (both versions recorded).
- **Writer runs async.** The page shows the template ("AI draft pending") until a validated draft
  replaces it. Hard limit 140 words (target 90-110), one retry that states the exact cut. It may
  not name another posture except in conditional, negated or threshold sentences, nor use numbers
  absent from the facts.
- **Lead-time confidence cap:** forecasts over 24 h ahead cap confidence at medium, over 48 h at
  low. The reason that set the level is listed first.
- **Operating calendar:** Mon-Fri minus US federal holidays, including **Columbus Day**. Today
  (Sat 2026-10-10) the default target is **Tue Oct 13**: Mon Oct 12 is Columbus Day. Use
  DEMO_MODE "today as a workday" or pick a day to demo against the current weather.
- **Ramp closures and lane restrictions are minor:** they don't close the facility. Closures only
  count when on the item's own route and near an access segment; corridor conditions show in the
  people summary as minor.
- Weather agent timeout 45 s → the same NWS tools run in code. Agent notes on `unknown` evidence
  are replaced by "Data missing".
- Coordinator over budget drops news first, then schools. Coordinator skipped when no hazards.
- Message agent: one call per notice, personal facts via placeholders; skipped if nobody is
  notified. Without `NTFY_TOPIC`/`RESEND_API_KEY` channels print to the console.
- Background roads runs at most once per 10 min; watcher every 5 min.
- Corridor privacy: map cells need ≥3 corridors and are dropped within 3 km of any home.
- `compose.prod.yml` SQLite file is `closure_call_v2.db`; the v1 file is left untouched in the
  volume.

## Known issues

- **`.env` is tracked in git** (added before `.gitignore` covered it) and contains the team
  inference key. It should be removed from the index (`git rm --cached .env`), added to
  `.gitignore`, and the key rotated. Ask the user before doing this; never print the file.
