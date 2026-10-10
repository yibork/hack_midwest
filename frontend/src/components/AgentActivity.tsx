import { useEffect, useRef, useState } from "react";
import { api, type RecordEvent } from "../api";
import { ErrorBox, fmtTime } from "./ui";

const AGENT_LABEL: Record<string, string> = {
  weather_agent: "Weather agent",
  writer_agent: "Writer agent",
  notify_agent: "Notify agent",
};

/** Live feed of one evaluation's agent activity. Polls every second while `live` is true. */
export default function AgentActivity({ evaluationId, live }: { evaluationId?: string | null; live: boolean }) {
  const [events, setEvents] = useState<RecordEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLLIElement>(null);

  useEffect(() => {
    setEvents([]);
    setError(null);
    if (!evaluationId) return;
    let cancelled = false;
    const load = () =>
      api.agentActivity({ evaluation_id: evaluationId })
        .then((e) => !cancelled && (setEvents(e), setError(null)))
        .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    load();
    // Keep polling while live; one last fetch after it stops picks up the final events.
    const timer = live ? setInterval(load, 1000) : undefined;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [evaluationId, live]);

  useEffect(() => {
    if (live) bottom.current?.scrollIntoView({ block: "nearest" });
  }, [events.length, live]);

  const lines = events.map(describe).filter((l): l is Line => l !== null);
  return (
    <aside className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-600">Agent activity</h2>
        {live && (
          <span className="flex items-center gap-1 text-xs font-semibold text-green-700">
            <span className="h-2 w-2 animate-pulse rounded-full bg-green-600" /> live
          </span>
        )}
      </header>
      <div className="max-h-[70vh] overflow-y-auto p-3">
        {error && <ErrorBox error={error} />}
        {!evaluationId && <p className="text-sm text-slate-500">Run an evaluation to see agents work.</p>}
        {evaluationId && lines.length === 0 && !error && <p className="text-sm text-slate-500">Waiting for activity…</p>}
        <ol className="space-y-1.5">
          {lines.map((l) => (
            <li key={l.id} className={`border-l-2 pl-2 text-xs ${TONE[l.tone]}`}>
              <div className="flex justify-between gap-2">
                <span className="font-semibold">{l.title}</span>
                <span className="shrink-0 tabular-nums text-slate-400">{fmtTime(l.time)}</span>
              </div>
              {l.detail && <div className="break-words text-slate-600">{l.detail}</div>}
            </li>
          ))}
          <li ref={bottom} />
        </ol>
      </div>
    </aside>
  );
}

type Tone = "agent" | "tool" | "data" | "engine" | "warn" | "block" | "muted";
const TONE: Record<Tone, string> = {
  agent: "border-indigo-400",
  tool: "border-sky-400",
  data: "border-slate-300",
  engine: "border-purple-500",
  warn: "border-amber-500 bg-amber-50",
  block: "border-red-600 bg-red-50",
  muted: "border-slate-200 text-slate-500",
};
interface Line { id: number; time: string; title: string; detail?: string; tone: Tone }

function describe(e: RecordEvent): Line | null {
  const p = e.payload as Record<string, any>;
  const who = AGENT_LABEL[p.agent] ?? p.agent ?? "Agent";
  const base = { id: e.id, time: e.timestamp };
  switch (e.event_type) {
    case "evaluation.started":
      return { ...base, title: "Evaluation started", tone: "muted" };
    case "agent.invocation_start":
      return { ...base, title: `${who} started`, tone: "agent" };
    case "agent.invocation_end":
      return { ...base, title: `${who} finished`, tone: "agent" };
    case "agent.model_call_start":
      return null; // the end event carries the useful part
    case "agent.model_call_end":
      return { ...base, title: `${who} · model`, detail: p.summary, tone: "muted" };
    case "agent.tool_call_start":
      return { ...base, title: `${who} → ${p.tool}`, tone: "tool" };
    case "agent.tool_call_end":
      return { ...base, title: `${who} ← ${p.tool}`, detail: p.summary, tone: "tool" };
    case "agent.evidence_note":
      return {
        ...base, title: `${who}: ${p.note}`, tone: p.ambiguity_flags?.length ? "warn" : "agent",
        detail: p.ambiguity_flags?.length ? `Flagged: ${p.ambiguity_flags.join("; ")}` : undefined,
      };
    case "agent.tool_backfill":
      return { ...base, title: `${who} skipped ${p.missing.join(", ")}; fetched directly`, tone: "warn" };
    case "agent.send_backfill":
      return { ...base, title: `${who} missed ${p.missed.length}; template sent`, detail: p.missed.join(", "), tone: "warn" };
    case "evidence.fetched":
      return { ...base, title: `NWS ${String(p.kind).replace(/_/g, " ")}: ${p.status}`, tone: p.status === "ok" ? "data" : "warn" };
    case "agent_unavailable":
      return { ...base, title: `${who} unavailable → ${p.fallback}`, detail: p.reason, tone: "warn" };
    case "writer_accepted":
      return { ...base, title: "Writer agent: rationale drafted", detail: `${p.words} words, validated`, tone: "agent" };
    case "writer_rejected":
      return { ...base, title: "Writer draft rejected → template", detail: p.reason, tone: "warn" };
    case "recommendation.created":
      return {
        ...base, title: `Rules engine: ${String(p.posture).replace("_", " ").toUpperCase()}`,
        detail: `${p.confidence} confidence · ${p.triggers.length} trigger(s) · rationale: ${p.rationale_source}`, tone: "engine",
      };
    case "decision.recorded":
      return { ...base, title: `Decision: ${p.action} by ${p.manager_name} → ${p.final_posture}`, tone: "engine" };
    case "dispatch_blocked":
      return { ...base, title: "Dispatch blocked: no approval", detail: `${p.tool} cancelled`, tone: "block" };
    case "notification.sent":
      return {
        ...base, title: `Sent to ${p.name} via ${p.channel}`,
        detail: `${p.drafted_by === "notify_agent" ? "drafted by notify agent" : "template"} · ${p.delivered ? "delivered" : "not delivered"}`,
        tone: "data",
      };
    case "notification.skipped":
      return null;
    default:
      return { ...base, title: e.event_type, tone: "muted" };
  }
}
