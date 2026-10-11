# Architecture

## Manager workflow and read-only audit

The Facility screen shows the posture, people/staffing and nearest two thresholds first;
source detail, maps, rationale and activity are expandable with browser-local preferences.
Decide is Review → Approve/override → Send/schedule. Delivery and acknowledgments poll every
three seconds; scheduled sends use the stored approved notice and existing scheduler guard.
Demo windows accept `?role=facility_manager|supervisor|employee|security_desk|auditor` plus
`&person=<employee id>`. These are demo roles, not authentication. Auditor routes are limited
to read-only Record, memos, Thresholds and saved evidence; write routes return 403.

`POST /api/recommendations/{id}/collect-evidence` returns an investigation ID and a linked
preliminary recommendation immediately. The existing investigation worker streams activity,
refreshes all allowlisted feeds in code, and checks EAX AFD/HWO (see sources.md). New/changed
items alone go to specialists; a verified forecaster summary uses at most one additional
model call against the same persistent 12-call budget. Exhaustion does not block fetching or
re-evaluation. The final revision preserves the original target-day explanation and reports
source statuses plus a plain-language diff. Unexpected failure finalizes partial evidence
with an explicit unknown-data flag, allowing a safe retry. Replay/simulation never fetch live.
No tables or columns are removed, and historical evidence, snapshots and record events are
untouched. Confidence-only facility policy 1.0.1 retains all posture thresholds and lead caps.

How an evaluation runs, which agents do what, and what is cached, rate-limited and scheduled.
The rule throughout: **agents gather evidence and word explanations; deterministic code decides;
a manager approves.** Source research and the local-signal allowlist are in
[sources.md](sources.md).

## Pipeline

`backend/app/pipeline.py:evaluate_facility()` runs one evaluation under a single `evaluation_id`.

1. **Target day.** By default, the next operating day: Mon-Fri, skipping US federal holidays
   (`app/calendar.py`), rolling over at close time. The manager can pick a day, and DEMO_MODE
   adds "today as a workday". The recommendation states the day and why.
2. **Evidence, in parallel:**
   - **Weather agent:** calls the NWS tools (point, alerts, hourly and grid forecast). The tools
     store Evidence rows, and the agent adds relevance notes. If the agent fails or times out
     (45 s), the same tools run in code. A response already stored in this evaluation is reused
     (same snapshot hash).
   - **Local signals, phase A:** coordinator, allowlisted source fetches, keyword pre-filter,
     road-facts cache lookup. Fast; no model call on a cache hit.
   - **People exposure:** one NWS `/alerts/active?zone=` request per unique employee home zone.
3. **Rules engine** (`rules/engine.py:evaluate()`, pure):
   - Weather rules, then local rules (`rules/local.py`); the most restrictive posture wins, and
     local signals can never lower it.
   - Confidence considers missing or stale data, ambiguity flags, near-threshold values,
     corroboration, and the forecast lead-time cap (over 24 h: at most medium; over 48 h: low).
     The reason that set the level is listed first.
4. **Store the recommendation.**
   - **Final** when all local facts are in.
   - **Preliminary - road check in progress** when the roads agent must run live (cache miss).
     Decisions are refused (HTTP 409) until a background thread re-runs the engine on the
     complete evidence, updates the recommendation in place and records both versions
     (`recommendation.finalized`).
5. **After final:**
   - The writer agent drafts the rationale in the background. The page shows the template,
     labelled "AI draft pending", and a validated AI draft replaces it later; both versions are
     recorded (`rationale.updated`).
   - The re-evaluation jobs are scheduled.
   - Message wording is pre-drafted for the notice.

Everything above writes RecordEvents (append-only), so an evaluation can be replayed.

RecordEvents now have per-facility hash chains with additive historical sealing, checkpointed
concurrent appends, and `/api/record/verify`. Raw saved responses remain inert text; their wrapper
adds provenance and byte verification. Printable decision memos retain the decision-time hash
and exclude private employee needs. See [decision-record.md](decision-record.md) for canonical
hash format, migration limitations, normalized historical copies and memo contents.

## Policy provenance

Policy 1.0.0 uses structured, verified citations for EAX criteria, CAP, NOAA KCDM7 and OPM.
`/api/thresholds` and the Thresholds page expose the version/hash, change log and every rule or
reference. The loader rejects a non-draft version with unverified entries. Facility posture
mappings, confidence caps, calendar, access and staffing choices are documented in
[facility-policy.md](facility-policy.md). FEB coordination is a manager reference with historical
OPM provenance; no current status feed was verified. Old saved citation strings remain readable.

## Agents

All agents are Strands agents on the Akamai inference service (`agents/model.py`, `runtime.py`).
Every model call and tool call is audited (`hooks.py: AuditHook`); `DispatchGuard` cancels any
`send_*` tool without a stored decision.

| Agent | Does | Can't |
|---|---|---|
| `weather_agent` | Calls the NWS tools and writes relevance notes / ambiguity flags | Change values; notes on `unknown` evidence are replaced by "Data missing" |
| `local_coordinator` | Chooses local agents for the active hazards. Skipped (rules plan) when no hazards are active | Exceed the call budget: news is dropped first, then schools |
| `local_{roads,schools,public_safety,news}_agent` | Extract `LocalFacts` from pre-filtered items. One call each, no tools, source text treated as untrusted | Decide relevance: code checks quotes, routes, the item's own route for closures, ramp/lane guards, and location (`app/geo.py`) |
| `writer_agent` | Rationale paragraph with `stated_posture`. Hard limit 140 words (aims for 90-110); one retry that states the exact count to cut | Name another posture except in conditional, negated or threshold sentences; use numbers not in the facts |
| `message_agent` | One call per notice drafts a template per recipient group; personal facts go in by placeholder | Choose recipients, instructions, times or access. Skipped entirely when nobody gets a message |

When the model is unavailable, every agent has a deterministic fallback. Roads fall back to code
extraction from the official DOT text.

## Caching

| What | Key | Lifetime |
|---|---|---|
| NWS responses | URL | 2 min, in memory (`sources/nws.py`) |
| Local source fetches | source id | the source's `refresh_minutes` (5-30), in memory |
| robots.txt | host | 6 h |
| Evidence within an evaluation | kind + snapshot hash | the evaluation; a repeat fetch reuses the row |
| **Road facts** | facility + target day + geometry (access segments, commute corridors) + each road source's **content hash** | until any of those changes (`road_facts_cache` table) |
| Message drafts | hash of a recipient group's facts | permanent (`message_drafts` table) |

**Road facts cache** (`agents/local/roads_cache.py`):
- **Key.** The content hash covers each road source's *parsed, area-filtered items*: item id,
  title, text, coordinates and start time. It does not hash the raw response, because MoDOT
  regenerates its feed every minute with new record IDs (705 of 1,003 records "changed" in 75 s
  with no real change), and KC Scout's response also carries live travel-time signs. Item IDs
  are content-based for the same reason.
- **Hit.** The validated facts are reused without a model call, and the record says "roads
  facts reused from <time> (sources unchanged)".
- **Background watcher** (`ROADS_WATCH`, every 5 min). It fetches the road sources for each
  facility's next operating day. If their content changed, it records `roads.sources_changed`
  and runs the roads agent then, at most once per 10 minutes, so evaluations usually hit the
  cache.
- **Miss during an evaluation.** The recommendation is shown as preliminary while the live
  check runs (see Pipeline).

## Rate limits

The team's inference quota is shared: 60 requests/min across all endpoints.

- **Process-wide limiter:** 45 requests/min (`LLM_REQUESTS_PER_MINUTE`), a sliding window in
  `agents/ratelimit.py`. Every model call goes through `GuardedOpenAIModel`.
- **On HTTP 429:** wait for `x-ratelimit-reset` (or 5 s), then retry, at most twice.
- **Per-evaluation budget:** 12 model calls (`LLM_CALLS_PER_EVALUATION`), shared by the
  weather, coordinator, local and writer agents. The coordinator keeps 2 calls for the writer.
  Over budget, `llm_budget_reached` is recorded and the deterministic path takes over.
- **Separate budget keys** for notices and drafting (`notice:<id>`, `predraft:<id>`) and for
  background roads runs (`roads-bg:<key>`).
- **Background roads runs:** at most 1 per 10 minutes.
- **NWS:** a descriptive User-Agent, the 2-minute cache, and one request per unique zone.

## Scheduled re-evaluations

`app/scheduler.py`, checked every minute (`SCHEDULER_LOOP`):

- **When.** Every recommendation schedules two jobs for its target day: 15:00 local the day
  before, and 04:00 on the day. The recommendation shows the next one.
- **What a job runs.** The *deterministic* pipeline: NWS tools in code, local signals with the
  rules plan, and road facts from the cache or code extraction. No model calls.
- **Then.** It compares posture, triggers and confidence with the latest recommendation for
  that day. When something changes, existing recommendations receive a linked deterministic
  revision, preserving the calendar explanation; the scheduled-job label is stored separately.
  A day with no earlier recommendation uses the normal evaluation pipeline. Either way it
  records `scheduled.recheck`.
- **Past days.** Jobs for days already gone are skipped.
- **Decision record.** Each decision records the forecast lead time (hours to the commute window).

## Map and privacy

`GET /api/recommendations/{id}/map` (manager only) feeds the Leaflet map on the recommendation and
Sources pages. It uses OpenStreetMap tiles, with attribution. It returns:
- the facility and its access segments;
- each local fact with its geometry, provenance, and links to the source and the saved copy
  (`/api/snapshots/{hash}`, served as plain text, never rendered);
- commute corridors aggregated into ~2 km cells.

**Corridor privacy.** A cell is shown only if at least 3 corridors cross it. Cells within 3 km of
any home are dropped, so no home location can be inferred. No API response contains home
coordinates, and a test checks every role's endpoints for them.

## Manager investigations

`POST /api/recommendations/{id}/investigate` returns an investigation ID immediately. The
manager thread polls `GET /api/investigations/{id}` (or the recommendation's `/investigations`
collection); tool progress uses the existing activity stream under that investigation ID.
`/suggestions` supplies deterministic context chips. The facility page and Decide page both
show the panel, source labels, cited evidence, policy links and revision differences.

All checks bind to the saved facility, **target date** and commute/work/departure windows.
The Strands investigator can call only fixed weather, river, commute/access-road and local
closure tools. Roads reuse the existing geographic relevance checks and road-facts cache.
No submitted URL is accepted. Record explanations make no tool or model calls. One-tap
checks, staff requests and scheduled rechecks remain deterministic when inference is unavailable.

Each recommendation chain has a persistent 12-model-call investigation budget, atomically
charged on every model turn (including retries). AI answers require inline known evidence/policy
citations, a target date, at most 120 words, supported numbers, and no nonconditional alternate
posture. Road answers must state a distance or explicitly say it is unknown. Rejected answers
get one retry with the exact validation reason, then a labeled deterministic summary.
Normalized source facts enter model context; fetched source text is never an instruction.

New evidence produces an additive Recommendation linked by `previous_recommendation_id`.
The pure engine reruns sufficiency, rules and lead-time confidence; the people engine supplies
recipient-count differences. A pending revision is preliminary and cannot be approved; neither
can its superseded predecessor. Audit events retain requests, tools, evidence, answers and diffs.
On restart, interrupted checks settle from stored evidence without resending staff requests.
Revisions preserve the root recommendation's `target_note` unchanged. Their `revision_context`
stores the investigation/recheck/staff/scheduled-job cause, question and timestamp separately.
Diffs ignore refreshed evidence IDs and citation bookkeeping; the UI hides unchanged checks and
describes meaningful trigger, confidence-reason and recipient changes in plain language.

Before a revision evaluates old evidence, expired source windows cause deterministic refetches
(NWS, NOAA river or allowlisted local sources), bypassing the affected HTTP cache. No inference
is used for freshness. Successful refreshes replace expired rows in the new recommendation;
failed refreshes retain an additive stale copy with the last successful retrieval time and a
refresh-failure note. Historical evidence is never edited. Record-only explanations still read
the saved record without tools.

Staff questions target assigned `facility_ops` people. ntfy HTTP-action buttons POST one offered
option to a single-use hashed token, expiring after two hours; an HTML form is the link fallback.
Replies become `human_reported` evidence with the person's name, role and timestamp. An unsafe
target-commute-window report invokes facility policy `site_hazard_reported` (delayed arrival);
safe replies cannot erase unsafe reports or lower a higher weather posture. With `DEMO_MODE=1`,
managers may answer on a persona's behalf; those reports are explicitly SIMULATED.

For real staff pushes, set `PUBLIC_BASE_URL` to the public HTTPS origin and configure each
facility-operations person's ntfy topic. Leave `DATABASE_URL` unset to retain production SQLite.
The optional SQLite-to-Postgres script is retained but is not run by startup or deployment.

### Demo scenarios and reset

`DEMO_MODE=1` exposes manager-only `/api/demo/scenario`, `/api/demo/reset`, recordings and
delivery-status routes. `/api/demo/state` supplies the global scenario banner. Seeded persona IDs
are stable; public ntfy topics append `DEMO_TOPIC_SUFFIX` once. Maria, Dana, Jordan, Sam, Lee,
Priya and Pat follow the presentation roster. Lee's already-teleworking instruction remains an
email-only FYI; Jordan is unscheduled and requires an explicit manager call-in.

Demo email delivery is restricted to the seven stable persona IDs and redirected to
`DEMO_EMAIL` using `RESEND_FROM`, with an addressed-to preface and `[Demo -> Name]` subject.
Other synthetic emails and pushes without a personal device are recorded as `simulated`,
not sent or failed, and do not start acknowledgment timers. Confirmation totals count channel
deliveries (push plus email), simulated channel attempts and failed channel attempts separately.
Provider failures retain a redacted message and HTTP status in the delivery record.

The winter scenario saves SIMULATED alerts, wind/ice, I-70 access and home-zone inputs, then
uses the unchanged policy and people engines. The synthetic clock is explicitly 04:00 local
on today (treated as a workday); 65 mph gusts meet the wind closure rule, while ice is context,
not an invented federal threshold. Replay clones references to saved raw bytes and preserves
original retrieval times. Neither mode runs live investigation tools. Live exposure uses only
the current recommendation's evidence IDs, including revisions; global demo injections cannot
leak into a live evaluation. The national scenario selects the most severe located active US
alert list for a presenter selection and evaluates a cloned synthetic facility there. The clone
has nullable `demo_origin_id`/`nws_office` metadata, translated synthetic access segments and
copied assignments/functions; employees, phone topics and KC are unchanged. See sources.md for
filtering, provenance, real target windows, local coverage limitations and policy 1.0.2.

Reset adds `archived_at` to active recommendation/decision/notice/notification/investigation/
staff-question rows and cancels pending jobs. It joins investigation and dispatch locks and
disables acknowledgment/escalation work for archived notifications. No rows, evidence, saved
copies or events are deleted. Historical decisions remain addressable for later memos; a
`demo_reset` event records the counts, and a new scenario starts a clean active view.
