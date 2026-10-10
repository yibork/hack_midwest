# Agents

**Agents gather evidence and write explanations. They never decide.** The posture comes from
`rules/engine.py:evaluate()` (deterministic, no LLM). A manager approves it, and nothing is sent
without a stored `Decision`. If the LLM is unreachable, everything still works without it.

Built on the [Strands Agents SDK](https://strandsagents.com/) (`strands-agents[openai]`, 1.59)
with the OpenAI-compatible provider pointed at the Akamai workshop service (`OPENAI_BASE_URL`,
`CHAT_MODEL=hackathon-chat`). The service supports chat completions with tool calls and JSON
schema only. Never use the Responses API, audio or image input.

| File | Role |
|---|---|
| `model.py` | `GuardedOpenAIModel`: limiter (45/min, process-wide), 429 → wait `x-ratelimit-reset` (2 retries), max_tokens 1024, temperature 0.1 |
| `ratelimit.py` | Sliding-window limiter and last-call status for `/api/health` |
| `runtime.py` | `make_agent()` attaches the hooks to every agent; `invoke()` enforces the timeout and raises `AgentUnavailable` |
| `hooks.py` | `LLMBudget` (12 model calls per evaluation → `llm_budget_reached`), `AuditHook` (a RecordEvent per invocation, model call and tool call), `DispatchGuard` (cancels `send_*` without a Decision) |
| `weather_agent.py` | Tools `fetch_alerts_for_facility`, `fetch_commute_window_forecast` store Evidence; the agent returns `{evidence_id, relevance_note, ambiguity_flags}` per row |
| `writer_agent.py` | Rationale paragraph (≤120 words); validated, else template + `writer_rejected` |
| `local/` | Local signals: `coordinator.py` picks agents within the call budget; `specialists.py` (roads, schools, public safety, news: one call each, no tools, LocalFacts schema only); `facts.py` validates every fact in code; `prefilter.py`; `run.py` |
| `message_agent.py` | Message wording only: ONE model call per notice drafts a template per recipient group (placeholders for personal facts); each rendered message is validated, else the deterministic template. No send tools |

## Guarantees and where they're enforced

- **Numbers come from Evidence rows.** The weather agent's tools store Evidence in code. The engine
  reads only those rows (`weather_agent.evidence_for(evaluation_id)`). The agent can change only
  `relevance_note` and `ambiguity_flags`. Flags can lower confidence to medium; they never change
  the posture.
- **Required data is always fetched.** If the agent skips a tool, `ensure_required_evidence()`
  calls it in code (`agent.tool_backfill`).
- **Writer can't change the outcome.** `validate_rationale()` rejects drafts that name a
  different posture, omit the recommended one, exceed 120 words, or contain a number that isn't
  in the facts given to it.
- **Nothing is sent without a Decision.** Sending is plain code (`notify/notices.py`), from a Notice
  created from a stored Decision. No agent has send tools; `DispatchGuard` still cancels any `send_*`
  tool call without a stored Decision (`dispatch_blocked`) as a second lock.
- **The message agent can't change who is told what.** It never sees names, homes or needs. It
  writes wording around `{instruction}`, `{access}`, `{why}`, … and every message must contain the
  duty instruction verbatim, only computed times, only numbers from the facts, and no emoji.
- **Late writes are refused.** `invocation_state["closed"]` is set when the caller stops
  waiting. Tools from an agent that timed out and is still running refuse to write.

## invocation_state

Agents are invoked as `agent(prompt, invocation_state={...})` (Strands 1.59 deprecates passing
these as plain kwargs). Keys: `evaluation_id`, `facility_id`, `llm_budget_key` (message drafting),
`closed`. Hooks read the ids from
`event.invocation_state`.

## Record events

`agent.invocation_start|end`, `agent.model_call_start|end`, `agent.tool_call_start|end`,
`agent.evidence_note`, `agent.tool_backfill`, `agent_unavailable`, `llm_budget_reached`,
`writer_accepted`, `writer_rejected`, `messages.drafted`, `dispatch_blocked`. All carry `evaluation_id`; the UI
polls `GET /api/agent-activity?evaluation_id=`.

## Check the endpoint first

```bash
cd backend && .venv/bin/python scripts/smoke_llm.py
```

It runs a plain chat, a Strands tool call and `GET /v1/models`, then prints `OK` or the failure
reason.
