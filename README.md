# Closure Call

Live: http://172.238.168.201

Decision support for a USDA facility manager: pull live National Weather Service data, recommend an
operating posture (normal / delayed arrival / early dismissal / telework / closure), let the manager
approve or override, notify the right employees, and keep a replayable decision record.

**Core principle:** agents gather evidence and write explanations; a deterministic rules engine
(plain Python, no LLM) produces the recommendation; a human approves; nothing is sent without an
approval record. If the LLM is unreachable, the app falls back to deterministic code.

```
pipeline.evaluate_facility (one evaluation_id)
  weather_agent ──tools──► sources/nws.py ──► Evidence rows (+ snapshots/{sha256}.json)
       └─ relevance notes / ambiguity flags only
  rules/engine.py:evaluate(facility, Evidence rows)   pure, deterministic ──► posture
  writer_agent ──► rationale, validated (else template)
manager approves / overrides ──► Decision
notify_agent ──send_push/send_email──► DispatchGuard + send_one() (Decision required)
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

## Configuration (root `.env`, see `.env.example`)

| Var | Purpose |
|---|---|
| `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `CHAT_MODEL`, `EMBEDDING_MODEL` | Akamai workshop inference |
| `OBJ_ENDPOINT`, `OBJ_BUCKET`, `OBJ_ACCESS_KEY`, `OBJ_SECRET_KEY` | Object storage (not used yet) |
| `DATABASE_URL` | Default SQLite at `SQLITE_PATH` (`./closure_call.db`; in production the `dbdata` volume) |
| `SITE_DOMAIN` | Production only: domain for Caddy's automatic HTTPS; empty means plain HTTP on :80 |
| `NWS_USER_AGENT` | Required by api.weather.gov; include a contact |
| `LLM_REQUESTS_PER_MINUTE`, `LLM_CALLS_PER_EVALUATION` | Defaults 45 / 12 |
| `AGENT_TIMEOUT_SECONDS`, `NOTIFY_AGENT_TIMEOUT_SECONDS` | Defaults 20 / 120; on timeout the deterministic path takes over |
| `NTFY_TOPIC` | Push via `https://ntfy.sh/{topic}`; subscribe in the ntfy app to see them |
| `RESEND_API_KEY`, `RESEND_FROM` | Email via Resend |
| `NOTIFY_EMAIL_OVERRIDE` | Demo: send every email to one inbox |
| `SNAPSHOT_DIR` | Raw NWS responses, default `./snapshots` |

If a channel isn't configured, the message prints to the server console and the notification is
stored with `delivered=false`.

## Layout

```
backend/app/
  main.py            FastAPI app, /api router, static frontend + SPA fallback
  api.py             all routes
  pipeline.py        weather agent -> rules engine -> writer agent, with fallback
  services.py        evidence storage, decision, recommendation detail
  config.py db.py models.py schemas.py
  sources/nws.py     NWS client: points, alerts (point + national), hourly, grid winds; 2-min cache
  rules/engine.py    evaluate(facility, evidence, now) -> Recommendation  (pure)
  rules/thresholds.yaml   starter rules; every citation is "TODO: verify"
  agents/            Strands agents, hooks (audit + dispatch guard), model, runtime
  notify/            recipients.py, messages.py (en/es), channels.py, dispatch()
  record.py          append-only RecordEvent helpers + snapshot storage
  seed.py            synthetic facility + roster
backend/tests/      test_rules.py (engine), test_agents.py (guard, writer validation, fallback)
backend/scripts/smoke_llm.py
frontend/src/
  api.ts             typed client mirroring schemas.py
  pages/             FacilityPage, DecidePage, NationalPage, RecordPage
  components/        RecommendationView, DecisionClock, AgentActivity, ui
```

## API (all under `/api`)

| Method | Path | |
|---|---|---|
| GET | `/health` | inference configured/missing, model alias, last model call ok |
| GET | `/facilities`, `/facilities/{id}` | |
| GET | `/facilities/{id}/latest-recommendation` | `null` if never evaluated |
| POST | `/facilities/{id}/evaluate` | body `{evaluation_id?}`; weather agent → rules → writer → stored Recommendation |
| GET | `/recommendations/{id}` | with evidence, decision, `decide_by` |
| POST | `/recommendations/{id}/decision` | `{manager_name, action, final_posture?, reason?}`; override requires reason; one decision per recommendation |
| GET | `/decisions/{id}/recipients` | everyone considered, included or not, with reason + drafted message |
| POST | `/decisions/{id}/notify` | notify agent (template fallback); idempotent |
| GET | `/decisions/{id}/notifications` | |
| GET | `/national-scan` | top 20 active US alerts (marine-only alerts skipped), with centroid |
| POST | `/evaluate-point` | `{lat, lon, name, evaluation_id?}` → temporary facility (07:30–16:00, NWS time zone) |
| GET | `/agent-activity?evaluation_id=&since=` | agent/tool/fallback events for an evaluation, in order |
| GET | `/record?facility_id=&since=&until=` | RecordEvents in order |
| GET | `/record/at?facility_id=&timestamp=` | latest recommendation, evidence and decision as of that moment |

## Schema changes

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
