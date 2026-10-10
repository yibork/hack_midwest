# Architecture

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
  that day. Only if something changed does it run the full pipeline with agents, which stores a
  new recommendation labelled "Scheduled re-evaluation". Either way it records
  `scheduled.recheck`.
- **Past days.** Jobs for days already gone are skipped.
- **Decision record.** Each decision records the forecast lead time (hours to the commute window).
