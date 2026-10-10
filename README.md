# Closure Call

Live: http://172.238.168.201

Decision support for a USDA facility manager: pull live National Weather Service data, recommend an
operating posture (normal / delayed arrival / early dismissal / telework / closure), let the manager
approve or override, notify the right employees, and keep a replayable decision record.

**Core principle:** agents gather evidence and write explanations; a deterministic rules engine
(plain Python, no LLM) produces the recommendation; a human approves; nothing is sent without an
approval record. Deterministic code also decides who is affected, what each person must do, who may
enter the building and who is told; the LLM only words the messages. Every per-person decision
carries machine-readable reason codes plus a human-readable reason. If the LLM is unreachable, the
app falls back to deterministic code.

```
pipeline.evaluate_facility (one evaluation_id)
  weather_agent ──tools──► sources/nws.py ──► Evidence rows (+ snapshots/{sha256}.json)
       └─ relevance notes / ambiguity flags only
  rules/engine.py:evaluate(facility, Evidence rows)   pure, deterministic ──► posture
  writer_agent ──► rationale, validated (else template)
  notify/exposure.py ──► one /alerts/active?zone= per unique employee home zone ──► Evidence
  message_agent (background) ──► message wording per recipient group, cached
manager approves / overrides ──► Decision ──► Notice (plan: impact, access, staffing, waves, drafts)
send ──► wave 1, wave 2, FYI ──► push / email (push fails → email) ──► acks ──► reminders/escalation
Every step, including every agent model/tool call ──► RecordEvent (append-only)
```

See `backend/app/agents/README.md` for the agent design and guarantees.

## Setup: inference credentials

The agents use the Akamai workshop inference service (OpenAI-compatible chat completions).

1. `cp .env.example .env` in the **repo root** and set `OPENAI_API_KEY` to the team key (`hk_...`).
   `OPENAI_BASE_URL`, `CHAT_MODEL` and `EMBEDDING_MODEL` already have the right values. The
   starter kit's `API_KEY` / `BASE_URL` names are also accepted.
2. Check it: `cd backend && .venv/bin/python scripts/smoke_llm.py`. It should print `OK` after
   (a) a plain chat, (b) a Strands tool call, (c) the model list. Failures name the cause:
   401 bad key, 404 wrong endpoint/model, 429 rate limited.
3. `GET /api/health` shows `inference: configured|missing`, the model alias and whether the
   last model call succeeded. It never shows the key.

`.env` is git-ignored and loaded with `override=True`, so an `OPENAI_API_KEY` in your shell from
another provider is never used. The key is redacted from errors, logs and the record. Without a
key, or if the service is down, the app runs deterministically (template rationale and messages).

**Quota (shared by the whole team):** 60 requests/min across all endpoints, 100k tokens/min,
32k context, 4,096 max output tokens. The backend caps itself at 45 requests/min, waits for
`x-ratelimit-reset` on a 429 (2 retries max), allows 12 model calls per evaluation (and 12 per
notify run), then falls back and records `llm_budget_reached`.

## Run in development

Backend (Python 3.11+), from `backend/`:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Tables are created and demo data (1 facility, 30 synthetic employees, today's and tomorrow's
schedule) is seeded on startup. Re-seed manually with `python -m app.seed`.

Frontend, from `frontend/`:

```bash
npm install
npm run dev                   # http://localhost:5173, proxies /api to :8000
```

Tests (no network; the live inference host is blocked): `cd backend && pytest`

## Run with Docker locally

```bash
docker compose up --build     # http://localhost:8000
```

This builds the frontend, serves it from FastAPI with SPA fallback, and uses the `postgres` service.
Credentials come from the root `.env` via `env_file`; they are not copied into the image. For any other host, set
`DATABASE_URL` and run the image; `postgres://` and `postgresql://` URLs both work.

## Deployment

### Architecture

```
GitHub push to main ──► CI (pytest + frontend build) ──► Deploy workflow ──ssh deploy@VM──► scripts/deploy.sh
                                                                                          │
Akamai Linode (Ubuntu 24.04, Chicago)   Cloud Firewall: 22, 80, 443                       ▼
  caddy (:80/:443, automatic HTTPS if SITE_DOMAIN is set) ──► app (FastAPI + built frontend, :8000)
                                                                │
                                                                ├─► Akamai Managed PostgreSQL (DATABASE_URL, sslmode=require)
                                                                │     or SQLite in the `dbdata` volume if DATABASE_URL is empty
                                                                └─► Akamai inference, api.weather.gov
```

`compose.prod.yml` runs two containers, `app` and `caddy` (`restart: unless-stopped`). It has no
database container. Data lives in the named volumes `snapshots`, `dbdata`, `caddy_data` and
`caddy_config`. Secrets stay in `~/closure-call/.env` on the server (mode 600); they are never in the
repo, the image or GitHub. The only value Caddy receives is `SITE_DOMAIN`.

### One-time setup

```bash
# On your machine
ssh-keygen -t ed25519 -N "" -f ~/.ssh/closurecall_deploy        # GitHub Actions → VM key (if missing)
scp scripts/server_setup.sh ~/.ssh/closurecall_deploy.pub root@<VM_IP>:
ssh root@<VM_IP> bash server_setup.sh closurecall_deploy.pub https://github.com/<owner>/<repo>
```

`server_setup.sh` is idempotent. It does the following:
- upgrades packages and adds 2 GB of swap (the frontend build needs it on a 2 GB VM)
- installs Docker
- creates the `deploy` user (in the `docker` group) with the Actions key
- clones the repo to `/home/deploy/closure-call`

If the repo is private, the script prints a public key and exits with code 2. Add that key under
GitHub > Settings > Deploy keys, read-only, then rerun the script.

Next, check that key login works for both users in fresh SSH sessions:

```bash
ssh -i ~/.ssh/closurecall_deploy deploy@<VM_IP> docker ps
ssh root@<VM_IP> true
```

Only after both work, turn off password login. The script runs `sshd -t` before restarting ssh:

```bash
ssh root@<VM_IP> bash server_setup.sh closurecall_deploy.pub https://github.com/<owner>/<repo> --harden
```

Then copy the secrets and do the first deploy:

```bash
scp .env deploy@<VM_IP>:closure-call/.env && ssh deploy@<VM_IP> chmod 600 closure-call/.env
ssh -i ~/.ssh/closurecall_deploy deploy@<VM_IP> closure-call/scripts/deploy.sh
curl http://<VM_IP>/api/health
```

If `/api/health` shows `"database": "unreachable"` and you're using Managed PostgreSQL, add the VM's
IP to the cluster's allow list: Cloud Manager > Databases > cluster > Networking.

Optional: put `SITE_DOMAIN=closurecall.example.com` in the server's `.env`, point the DNS A record at
the VM, and redeploy. Caddy then gets a certificate and serves HTTPS.

### GitHub secrets and variables

| Name | Kind | Value |
|---|---|---|
| `DEPLOY_HOST` | secret | VM public IP |
| `DEPLOY_USER` | secret | `deploy` |
| `DEPLOY_SSH_KEY` | secret | contents of `~/.ssh/closurecall_deploy` (private key) |
| `AUTO_DEPLOY` | variable | `true`; set `false` to stop automatic deploys after CI |
| `PUBLIC_URL` | variable (optional) | URL shown in the deploy job summary |

```bash
gh secret set DEPLOY_HOST --body <VM_IP>
gh secret set DEPLOY_USER --body deploy
gh secret set DEPLOY_SSH_KEY < ~/.ssh/closurecall_deploy
gh variable set AUTO_DEPLOY --body true
```

### Pipeline

The two workflows:
- `.github/workflows/ci.yml` runs on every push and pull request. It runs backend pytest on
  Python 3.11 with a dummy key and a mocked model, and builds the frontend on Node 20.
  `tests/conftest.py` fails any test that tries to resolve `ws.akamai-workshops.com`, because the
  inference quota is shared by the team.
- `.github/workflows/deploy.yml` runs when CI succeeds on a push to `main`, or by hand from
  Actions > Deploy > Run workflow. It SSHes to the VM as `deploy` and runs `scripts/deploy.sh`.
  Deploys never overlap (concurrency group `deploy`). The job summary shows the URL and the
  deployed commit.

**AUTO_DEPLOY switch:** `gh variable set AUTO_DEPLOY --body false` pauses automatic deploys. Manual
runs still work. Set it back to `true` to resume.

### Manual deploy and rollback

`scripts/deploy.sh` on the server does the following:
1. Resets to `origin/main`.
2. Runs `docker compose -f compose.prod.yml up -d --build`.
3. Polls `http://localhost/api/health` for up to 120 s, requiring HTTP 200 and `"inference": "configured"`.

If the check fails, the script resets to the previous commit, rebuilds, prints `ROLLED BACK` and exits 1.
Every run is appended to `~/deploys.log`.

```bash
ssh deploy@<VM_IP> closure-call/scripts/deploy.sh          # deploy origin/main now
ssh deploy@<VM_IP> cat deploys.log                          # history: time, commit, result
```

To roll back to a specific version, revert on `main` and push (`git revert <sha>`). The pipeline
then deploys the revert. In an emergency on the server:

```bash
cd ~/closure-call && git reset --hard <good-sha> && docker compose -f compose.prod.yml up -d --build
```

The next deploy returns the server to `origin/main`.

### Logs

```bash
ssh deploy@<VM_IP>
cd ~/closure-call
docker compose -f compose.prod.yml logs -f app      # application
docker compose -f compose.prod.yml logs -f caddy    # proxy / TLS
docker compose -f compose.prod.yml ps               # health status
```

## Local signals (roads, schools, public safety, news)

Four Strands specialist agents run under a coordinator, in parallel with the weather agent. Each
reads only allowlisted sources: [`backend/app/sources/registry.yaml`](backend/app/sources/registry.yaml),
with the research and skipped sources in [`docs/sources.md`](docs/sources.md). The **Sources** page
shows each source's last fetch.

- **Fetching** (`sources/local_signals.py`):
  - allowlist only; redirects are followed only within it
  - robots.txt is respected for our User-Agent and for AI agents
  - `refresh_minutes` cache, last 24 hours only, excerpts of at most 300 characters
  - a failed fetch is `unknown`, never "no closures"
- **Agents** (`agents/local/`):
  - a keyword pre-filter in code, then one model call per agent, structured output only, and no tools
  - source text is treated as untrusted
  - code validates every fact: the item exists, the quote is verbatim, routes appear in the item,
    and route matches are recomputed. A closure counts only for the item's own route, and lane,
    ramp, exit and weigh-station closures are not road closures.
  - roads fall back to code extraction when no model call is possible
- **Coordinator**:
  - flood → roads + news
  - winter → roads + schools + public safety
  - severe storm → public safety + news
  - nothing active → roads only
  - if the evaluation's call budget is short, news is dropped first, then schools
- **Effects** (`rules/local.py`, facility policy v1):
  - `access_route_closed` gives a delayed arrival, or telework if every access route is closed
    ("N of M access routes closed"). It needs an official state closure on an access route,
    within 15 miles, during the commute window.
  - commute-route closures go to the people engine as `ROUTE_CLOSED`
  - schools and official local items corroborate only alongside a fired weather rule
  - news is a "manager should check" note only
  - local signals never lower the posture

## People, access and notifications

Everything here is deterministic and unit-tested. The LLM never decides who is told what.

| Module | Decides |
|---|---|
| `notify/impact.py` | Per person, in order, with reason codes: assignment (`DETAIL_OUT`, `DETAIL_IN`), schedule (`LEAVE`, `DAY_OFF`, `TELEWORK_SCHEDULED`), shift overlap (`SHIFT_NOT_AFFECTED`), role (`EMERGENCY_DUTY`, `FACILITY_OPS`, `CONTRACTOR_STATUS_ONLY`, `SUPERVISOR_SUMMARY`, `SECURITY_ACCESS_LIST`, `SCHEDULED_ONSITE`), exposure (`HOME_IN_WARNING_AREA`, `ROUTE_CLOSED`), accommodations (shown only as `ACCOMMODATION_ON_FILE`) |
| `notify/instructions.yaml` | Duty instruction per posture × designation (+ contractors, facility ops actions), en/es |
| `access/policy.yaml` | Building access per posture × access level; the security desk's access list |
| `staffing/coverage.py` | Critical function coverage (`Animal care 1/2 - shortfall`) and ranked backups; never auto-assigns |
| `notify/delivery.py` | Waves, channels, decision clock (`first: Dana R., early shift 05:00, 40 min commute`) |
| `notify/plan.py` | Planned notifications, supervisor summaries, access list and staffing alert messages, notice versioning |
| `notify/notices.py` | Persist, send in waves, push → email fallback, call-ins, acks |
| `notify/escalation.py` | No ack: reminder at 15 min, supervisor at 30, facility manager at 45 (DEMO_MODE: 1/2/3 min) |
| `agents/message_agent.py` | Message wording: ONE model call per notice drafts one template per recipient group; personal facts go in by placeholder; every message is validated, else the template |

Key rules:
- Exposure never sends anyone home. The supervisor gets "consider offering unscheduled telework/leave
  per agency policy", and the employee gets an FYI that their supervisor has been informed. Exposed
  emergency staff are flagged in the staffing check instead. Home zones are resolved once at seed time.
  Each evaluation queries `/alerts/active?zone=` once per unique zone or county.
- `notify_by` is shift start − commute − 30 min for delays and closures, and dismissal time − 45 min
  for early dismissal. The decision clock is the earliest `notify_by`, and names the person who drives it.
- Wave 1: emergency staff, facility ops, accommodation early notice, call-ins, and anyone due within
  60 minutes. Wave 2: other action-required. FYI goes last, by email only. Before 06:00, push and email
  are both used.
- A changed decision creates a new Notice that supersedes the old one. Changed instructions get an
  "Update: now … (before: …), replacing the HH:MM notice". People no longer affected get an all clear.
  Identical messages are never resent.
- Roles (demo switcher, no real auth; headers `X-Role` / `X-Person`): the facility manager sees
  everything. A supervisor sees their own team only, as `audience_text`. The security desk sees the
  access list. An employee sees their own status. Accommodation needs never leave the engine:
  supervisors and managers only see "accommodation on file".
- `DEMO_MODE=1` shortens the ack timers and enables `/api/demo/simulate`, which injects a clearly
  marked simulated home-zone warning or road closure for showing exposure on a calm day.
- Demo personas (ntfy topics, QR codes on the Demo personas page):
  - `closurecall-demo-emergency`: emergency, animal care
  - `closurecall-demo-telework`: telework-ready, scheduled onsite
  - `closurecall-demo-teleworking`: already teleworking, so email only and no push
  - `closurecall-demo-exposed`: home in Cass County, MO; use the simulated warning for zone `MOZ043`

## Configuration (root `.env`, see `.env.example`)

| Var | Purpose |
|---|---|
| `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `CHAT_MODEL`, `EMBEDDING_MODEL` | Akamai workshop inference |
| `OBJ_ENDPOINT`, `OBJ_BUCKET`, `OBJ_ACCESS_KEY`, `OBJ_SECRET_KEY` | Object storage (not used yet) |
| `DATABASE_URL` | Default SQLite at `SQLITE_PATH` (`./closure_call.db`; in production the `dbdata` volume) |
| `SITE_DOMAIN` | Production only: domain for Caddy's automatic HTTPS; empty means plain HTTP on :80 |
| `NWS_USER_AGENT` | Required by api.weather.gov; include a contact |
| `LLM_REQUESTS_PER_MINUTE`, `LLM_CALLS_PER_EVALUATION` | Defaults 45 / 12 |
| `AGENT_TIMEOUT_SECONDS` | Default 20; on timeout the deterministic path takes over |
| `NTFY_TOPIC` | Fallback push topic for people without their own `ntfy_topic` |
| `PUBLIC_BASE_URL` | Base for ack links in messages (default `http://localhost:8000`; set the public URL in production) |
| `DEMO_MODE` | `1`: ack timers 1/2/3 min, `/api/demo/simulate` enabled |
| `RESOLVE_HOME_ZONES`, `PREDRAFT`, `ESCALATION_LOOP`, `LOCAL_SIGNALS` | Default `1`; tests set `0` (no network, no background threads) |
| `LOCAL_AGENT_TIMEOUT_SECONDS` | Default 45; per local agent model call (they run in parallel with the weather agent) |
| `RESEND_API_KEY`, `RESEND_FROM` | Email via Resend |
| `NOTIFY_EMAIL_OVERRIDE` | Demo: send every email to one inbox |
| `SNAPSHOT_DIR` | Raw NWS responses, default `./snapshots` |

If a channel isn't configured, the message prints to the server console and its channel result is
stored with `ok=false` and the reason.

## Layout

```
backend/app/
  main.py            FastAPI app, /api router, static frontend + SPA fallback
  api.py             manager routes; people_api.py: role-aware routes (team, access list, me, call-ins)
  roles.py           demo roles (X-Role / X-Person) and per-route role checks
  pipeline.py        weather agent -> rules engine -> writer agent, with fallback; exposure; predraft
  services.py        evidence storage, decision, recommendation detail
  config.py db.py models.py schemas.py
  sources/nws.py     NWS client: points, alerts (point + national), hourly, grid winds; 2-min cache
  rules/engine.py    evaluate(facility, evidence, now) -> Recommendation  (pure)
  rules/thresholds.yaml   starter rules; every citation is "TODO: verify"
  agents/            Strands agents (weather, writer, message), hooks (audit + dispatch guard), model, runtime
  notify/            impact.py, instructions.yaml, delivery.py, plan.py, notices.py, messages.py (en/es),
                     exposure.py, escalation.py, channels.py
  access/            policy.py + policy.yaml (building access)
  staffing/          coverage.py (critical functions, backups)
  record.py          append-only RecordEvent helpers + snapshot storage
  seed.py            synthetic facility, 40 people, shifts, assignments, critical functions
backend/tests/      test_rules.py (engine), test_agents.py (guard, writer, message agent, fallback),
                    test_impact.py (every reason code), test_access_staffing_delivery.py,
                    test_notices.py (versioning, call-ins, escalation, roles, privacy), test_inference.py
backend/scripts/smoke_llm.py
frontend/src/
  api.ts             typed client mirroring schemas.py
  pages/             FacilityPage, DecidePage, TeamPage, SecurityPage, MyStatusPage, PersonasPage,
                     NationalPage, RecordPage
  components/        RecommendationView, NoticePanel, People (coverage strip), DecisionClock,
                     RoleSwitcher/RoleContext, AgentActivity, ui
```

## API (all under `/api`)

| Method | Path | |
|---|---|---|
| GET | `/health` | inference configured/missing, model alias, last model call ok |
| GET | `/facilities`, `/facilities/{id}` | |
| GET | `/facilities/{id}/latest-recommendation` | `null` if never evaluated |
| POST | `/facilities/{id}/evaluate` | body `{evaluation_id?}`; weather agent → rules → writer → stored Recommendation |
| GET | `/recommendations/{id}` | with evidence, decision, `decide_by`, `people` (coverage, clock, exposed count) |
| POST | `/recommendations/{id}/decision` | `{manager_name, action, final_posture?, reason?, delay_hours?, dismissal_time?}`; override requires reason; creates the Notice (nothing sent) |
| GET | `/decisions/{id}/notice`, `/notices/{id}` | notice, every person considered (reason codes, wave, notify_by, channels, access, ack), coverage, clock |
| POST | `/decisions/{id}/notify` | send: wave 1, wave 2, FYI; idempotent |
| POST | `/notices/{id}/call-ins` | `{employee_id, function_code, manager_name}`; only ranked backups; sent at once, ack required |
| GET | `/notices/{id}/access-list`, `/facilities/{id}/access-list` | manager, security desk |
| GET | `/team` | supervisor: own team board (manager: `?supervisor_id=`) |
| GET | `/me` · POST `/notifications/{id}/ack` | employee: own status and ack |
| GET | `/ack/{notification_id}` (no `/api`) | the link in messages: records the ack, confirmation page |
| GET | `/viewers` | role switcher lists and demo personas (no private fields) |
| POST | `/demo/simulate` | DEMO_MODE only: `{kind: zone_alert\|road_closure\|clear, zone?, route?}` |
| GET | `/national-scan` | top 20 active US alerts (marine-only alerts skipped), with centroid |
| POST | `/evaluate-point` | `{lat, lon, name, evaluation_id?}` → temporary facility (07:30–16:00, NWS time zone) |
| GET | `/agent-activity?evaluation_id=&since=` | agent/tool/fallback events for an evaluation, in order |
| GET | `/record?facility_id=&since=&until=` | RecordEvents in order |
| GET | `/record/at?facility_id=&timestamp=` | latest recommendation, evidence and decision as of that moment |

## Schema changes

Production (`compose.prod.yml`) keeps SQLite at `closure_call_v2.db`, since the people/notice schema
replaced the old tables. The previous `closure_call.db` stays in the volume, untouched.

There are no migrations yet. After pulling model changes, delete `backend/closure_call.db` (dev) or
`docker compose down -v` (Postgres). Demo data is reseeded on startup.

## Rules engine notes

- **Operating date:** today until today's close time, then tomorrow.
- **Windows** (facility local time): commute = open − 2h → open; operating hours = open → close;
  departure = close → close + 2h. Alert rules for wind and flood apply across the whole operating day
  (commute start → departure end).
- Gust rules use the commute window. Once it has passed for today they can't fire; the departure-window
  peak is reported in the rationale only.
- Most restrictive posture wins: closure > telework > early_dismissal > delayed_arrival > normal.
- **Confidence:** any `unknown` evidence (or a missing required source) → low; stale evidence,
  alert certainty other than Observed/Likely, a gust within 5 mph of a threshold, or a weather-agent
  ambiguity flag → medium.
  Missing data is never treated as safe.
- `policy_version` is the YAML `version` plus a hash of the file, so replay shows the exact rules used.
- No weekend or holiday calendar yet: every day is treated as an operating day.
