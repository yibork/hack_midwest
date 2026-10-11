<p align="center">
  <img src="assets/closure-call-logo.png" alt="Closure Call" width="640" />
</p>

### Make the call. Reach the right people. Keep the reasons.

**[Try the live app](https://closurecall.duckdns.org/facility)** · [Live national alerts](https://closurecall.duckdns.org/national) · [Thresholds and citations](https://closurecall.duckdns.org/thresholds) · [Decision record](https://closurecall.duckdns.org/record)

It’s 4 a.m. Severe weather is approaching, and employees will soon leave home. A facility manager must decide: **open normally, delay, dismiss early, offer telework or close?**

The answer is scattered across weather alerts, road reports and employee schedules. Closure Call brings that evidence together so the manager can make an informed decision and communicate it before the commute begins.

> **Agents investigate. Code applies documented rules. A human makes the final call.**

Built for the **USDA Closure Call**, **AWS Best Use of Strands Agents SDK**, and **Akamai Cloud AI** challenges at [Hack Midwest](https://hackmidwest.com/).

## Start here: 

Use the **facility manager** demo role. This is a prototype with synthetic employee records and a role switcher.

1. **Show real NWS data.** Open [National scan](https://closurecall.duckdns.org/national), refresh, and select a located active alert. Inspect its source, timestamps, severity, certainty and urgency. This evaluates a temporary facility; it does not have an employee roster.
2. **Show the complete facility workflow.** Open [Facility](https://closurecall.duckdns.org/facility), choose the seeded Kansas City demo facility and evaluate a target operating day. Show the graded recommendation, threshold observations and margins, confidence, missing data and what would change the result. Wait for preliminary local checks to finish before deciding.
3. **Show agents doing work.** Follow the agent activity panel and ask an investigation question such as “Is the access road still closed?” Inspect the supporting evidence and any revised recommendation. 
4. **Approve and notify.** Open [Decide] (https://closurecall.duckdns.org/decide), approve or override with a reason, then inspect who is included or excluded, their instructions and channel routing. Send to configured demo recipients and show an actual receipt and acknowledgment. Unconfigured channels are not successful deliveries.
5. **Show accountability.** Open [Record](https://closurecall.duckdns.org/record), reconstruct the decision, inspect a saved evidence copy, verify its hash and open the printable decision memo. Follow a threshold link to its citation and facility-policy justification.

<!-- **Live evidence and scenario playback are separate.** Use the national scan for the required real-alert demonstration even if Kansas City has calm weather. A labeled simulated winter scenario or saved replay can demonstrate the roster workflow, but does not replace the live NWS requirement. -->

## How it works

![Closure Call decision workflow](../closure-call-architecture.png)

*Solid arrows show the workflow. Dashed arrows show information saved to the decision record.*

1. Gather weather and supplementary evidence alongside facility details and employee schedules.
2. Apply documented rules and explain the recommendation, uncertainty and alternatives.
3. Let the manager investigate further before making the decision.
4. Record the decision and send instructions appropriate to each affected person.
5. Preserve the evidence, reasoning, decision and notification outcomes.

An onsite employee may need a new reporting time. Someone on leave may need no routine alert. Essential staff receive instructions specific to their responsibilities. Assignments, details, telework status, accessibility needs and commute timing inform the notification plan.

<!-- The main demonstration uses a **synthetic USDA Kansas City field office**, focusing on wind/severe-weather and flooding/access disruption. A national scan demonstrates live evidence at other locations; it is not a claim that every facility or hazard is fully modeled. -->

## USDA: the five deliverables

| Required deliverable | What Closure Call implements | Where to inspect it |
| --- | --- | --- |
| **Working system with live NWS data** | Calls the actual NWS API for alerts and forecasts; combines it with supplementary road and local signals, plus river checks during investigation | [National scan](https://closurecall.duckdns.org/national), [NWS client](../backend/app/sources/nws.py), [source documentation](../docs/sources.md) |
| **Recommendation with reasoning** | Normal, delayed arrival, early dismissal, telework or closure; source-linked and timestamped evidence; observed values, thresholds and numeric margins where applicable; confidence, what would change the result and a plain-language rationale | [Rules engine](../backend/app/rules/engine.py), [recommendation view](../frontend/src/components/RecommendationView.tsx) |
| **Notification package** | Recipient inclusion/exclusion reasons, drafted instructions, push/email routing, notification timing, acknowledgment and escalation | [Notification plan](../backend/app/notify/plan.py), [delivery channels](../backend/app/notify/channels.py), Decide and Teams pages |
| **Reconstructable decision record** | Saved evidence, evaluation and decision history, overrides, delivery outcomes, hash verification and printable decision memos | [Decision-record documentation](../docs/decision-record.md), [record implementation](../backend/app/record.py) |
| **Defensible thresholds** | Versioned policy, structured source citations, published hazard definitions and explicit justification for the prototype’s response choices | [Thresholds page](https://closurecall.duckdns.org/thresholds), [policy YAML](../backend/app/rules/thresholds.yaml), [facility policy](../docs/facility-policy.md) |

Official hazard definitions inform the thresholds; **facility policy determines the proposed response**. These response mappings are prototype choices, not official USDA directives. News alone does not trigger closure, and missing data is marked unknown. Verification of a source definition does not imply agency endorsement of our response.

## AWS: how we use Strands Agents SDK

![Strands agents connected to Akamai AI inference, with rules-based recommendations and human approval](../closure-call-strands-agents.png)

**Strands provides the agent framework. Akamai provides the AI inference.** The application directly depends on `strands-agents[openai]` and creates real Strands `Agent` instances.

| Agent | Role in the workflow |
| --- | --- |
| **Weather** | Calls Python tools exposed with `@tool` to fetch and store evidence, then returns relevance notes and uncertainty flags |
| **Local coordinator and specialists** | Select and analyze relevant roads, schools, public-safety and news items; Python validates the extracted facts |
| **Investigator** | Uses bounded tools to check access, commutes, river conditions and local closures, or explains saved evidence in response to manager questions |
| **Writer and message agents** | Draft explanations and recipient-group wording from validated facts; code retains control of recommendations, recipients and instructions |

Our custom **agent harness** adds execution context, structured output models, call budgets, rate limiting, timeouts, result validation and audit hooks. Failed calls or rejected drafts fall back to direct fetching, code-based extraction or templates as appropriate. Agents have no notification-sending tools; application code sends notices after a stored manager decision.

The distinctive workflow connects **manager questions → tool-based investigation → validated evidence → a rules-based revision → human review**, while preserving the reasons and employee-specific consequences.

Implementation: [agent runtime](../backend/app/agents/runtime.py) · [weather tools](../backend/app/agents/weather_agent.py) · [investigator](../backend/app/investigations.py) · [audit and budget hooks](../backend/app/agents/hooks.py) · [agent documentation](../backend/app/agents/README.md).

## Akamai: cloud hosting and AI inference

The project’s cloud deployment uses **Akamai Cloud hosting and the Akamai workshop AI inference endpoint**. Inference is configured through `OPENAI_BASE_URL`, `OPENAI_API_KEY` and `CHAT_MODEL`; the OpenAI-compatible interface does not mean the application calls OpenAI-hosted models.

| Judging area | Project contribution and evidence |
| --- | --- |
| **Impact and usefulness** | Helps managers act before employee commutes, communicate individual duties and retain a reconstructable decision record |
| **Powered by Akamai** | Cloud-hosted application and AI inference through the [model adapter](../backend/app/agents/model.py); [production Compose](../compose.prod.yml) and [server setup](../scripts/server_setup.sh) define the hosting setup |
| **Technical execution** | Frontend/backend CI checks, persisted evidence, validated AI outputs, fallbacks, health reporting and deployment rollback logic |
| **Creativity and innovation** | Combines interactive investigation, employee-aware notification planning and verifiable saved evidence around a human decision |

PostgreSQL is supported through `DATABASE_URL`; when unset, production uses SQLite in a persistent volume. **Managed PostgreSQL is optional, not a required deployed service.** Raw evidence snapshots live on persistent disk; object-storage settings exist but object storage is not implemented.

## CI/CD and deployment

![Closure Call CI/CD pipeline, cloud hosting and database connections](../closure-call-cicd-cloud.png)

[CI](../.github/workflows/ci.yml) runs backend tests, frontend tests and the frontend build on pushes and pull requests. [Deployment](../.github/workflows/deploy.yml) can run after successful CI on a push to `main`, unless `AUTO_DEPLOY=false`, or be triggered manually.

The deployment connects over SSH and runs [deploy.sh](../scripts/deploy.sh). Docker builds React and packages it with FastAPI; Caddy forwards public traffic and supports HTTPS for a configured domain. A failed deployment health gate triggers an attempt to restore the previous code version.

Server setup uses `~/closure-call` and an external environment file, defaulting to `/home/deploy/closure-call.env` (mode `600`). Set `SITE_DOMAIN` and `PUBLIC_BASE_URL` for the public origin, plus inference and any delivery credentials. GitHub repository secrets are `DEPLOY_HOST`, `DEPLOY_USER` and `DEPLOY_SSH_KEY`; `PUBLIC_URL` is an optional repository variable for the deployment summary. See [server_setup.sh](../scripts/server_setup.sh) and [compose.prod.yml](../compose.prod.yml).

The deployment script currently fetches the latest `main`, rather than pinning the exact CI-tested commit. Its health gate checks HTTP availability and inference configuration; it does not prove a successful model call or require a healthy database result. Code rollback does not reverse database changes.

## Run locally

Prerequisites: Docker with a recent Docker Compose v2. Run from the repository root (the parent of this `backend` directory):

```sh
cp .env.example .env
# Edit .env with your Akamai workshop credentials and NWS contact User-Agent.
docker compose up --build
```

Open **http://localhost:8000**. Local Compose starts the application and PostgreSQL; startup creates tables and seeds synthetic demo records. No API key is required for NWS. Without valid inference configuration, AI stages use their fallbacks.

For actual delivery, configure `NTFY_TOPIC` or recipient topics and/or `RESEND_API_KEY` with a valid `RESEND_FROM`. `NOTIFY_EMAIL_OVERRIDE` can direct demo email to one test inbox. Set `PUBLIC_BASE_URL` to an address recipients can reach for acknowledgment links. Optional `DEMO_MODE=1` enables labeled scenarios and shortened demonstration timers. Never commit `.env` or credentials.

### Tests

These mirror the checks configured in CI; they are commands to run, not a claim about the latest test result.

```sh
# Backend: Python 3.11, from backend/
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

```sh
# Frontend: Node 20, from frontend/ (matching CI)
npm ci
npm test
npm run build
```

Backend tests use a temporary SQLite database and block calls to the live workshop inference endpoint. Test coverage includes rules, agents, recipient planning, investigations, saved copies and record integrity. Live NWS, inference and notification delivery must also be demonstrated separately.

### Schema changes

Startup creates missing tables and adds missing nullable columns. Renames, type changes and new non-nullable columns require a deliberate migration. The optional [SQLite-to-PostgreSQL migration script](../backend/scripts/migrate_sqlite_to_postgres.py) is not run automatically by deployment.

## Before presenting

- Demonstrate a fresh national NWS alert with its source and timestamp; a simulated or replayed scenario is not a substitute.
- Show a successful Strands tool/model run in the activity panel and identify any fallback output.
- Show the Akamai compute deployment and successful inference use without exposing credentials; confirm the actual database backend through `/api/health`.
- Test a real push or email to a demo recipient and show acknowledgment. A draft or console fallback is not proof of receipt.
- Open the saved decision memo and threshold citations. Hash chains are tamper-evident, not tamper-proof; their limitations are documented below.

These are presentation checks, not additional sponsor requirements. Current deployment availability and provider success should be verified before judging.

## Documentation and resources

[Architecture](../docs/architecture.md) · [Decision record and printable memos](../docs/decision-record.md) · [Facility policy](../docs/facility-policy.md) · [Data sources](../docs/sources.md) · [Design before/after](../docs/design-pass.md)

[Hack Midwest challenges](https://hackmidwest.com/) · [Strands SDK](https://github.com/strands-agents/sdk-python) · [Akamai starter kit](https://github.com/akamai-developers/akamai-workshop-hackmidwest) · [NWS API](https://www.weather.gov/documentation/services-web-api) · [NOAA water API](https://water.noaa.gov/about/api)
