import { useEffect, useRef, useState } from "react";
import { api, type RecordEvent } from "../api";
import { ErrorBox, fmtTime, humanText, postureLabel } from "./ui";

const AGENT_LABEL: Record<string, string> = {
  weather_agent: "Weather",
  investigator_agent: "Investigator",
  writer_agent: "Writer",
  message_agent: "Writer",
  local_coordinator: "Local coordinator",
  local_roads_agent: "Roads",
  local_schools_agent: "Schools agent",
  local_public_safety_agent: "Public safety agent",
  local_news_agent: "News agent",
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

  const lines = events.map(e => {
    const line = describe(e);
    if (!line) return null;
    const p = e.payload as Record<string, any>;
    const group = e.event_type.startsWith('investigation') || e.event_type === 'recommendation_revised' ? 'Investigator'
      : /writer|message/.test(p.agent ?? '') || /writer|rationale|messages/.test(e.event_type) ? 'Writer'
      : /road|local/.test(p.agent ?? '') || e.event_type.startsWith('local.') || p.source === 'local' ? 'Roads' : 'Weather';
    const tools = /tool_call|tool_backfill|refresh/.test(e.event_type);
    const running = live && (e.event_type === 'agent.tool_call_start' || e.event_type === 'agent.invocation_start') && !events.some(end => end.id > e.id &&
      end.payload.agent === p.agent && (tools ? end.event_type === 'agent.tool_call_end' && end.payload.tool === p.tool : end.event_type === 'agent.invocation_end'));
    return {...line, group, tools, running};
  }).filter((l): l is Line & {group: string; tools: boolean; running: boolean} => l !== null);
  const groups = [...new Set(lines.map(l => l.group))];
  const step = (l: typeof lines[number]) => <li key={l.id} className={`timeline-step text-sm ${TONE[l.tone]}`}>
    <div className="flex justify-between gap-2"><span>{l.running ? <span aria-label="Running" className="mr-2 inline-block h-3 w-3 animate-spin border-2 border-slate-300 border-t-blue-700" /> : <span aria-hidden="true" className="mr-2">{l.tone === 'warn' || l.tone === 'block' ? '⚠' : '✓'}</span>}{humanText(l.title)}</span>
      <time className="shrink-0 text-xs text-slate-500">{fmtTime(l.time)}</time></div>
    {l.detail && <p className="mt-1 break-words text-sm text-slate-600">{plainSummary(l.detail)}</p>}
  </li>;
  return (
    <aside className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-2">
        <h2 className="text-lg font-semibold text-slate-800">How this was decided</h2>
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
        <div className="space-y-3">{groups.map(group => <section key={group} aria-label={`${group} timeline`}>
          <h3 className="mb-1 text-sm font-bold">{group}</h3>
          <ol>{lines.filter(l => l.group === group && !l.tools).map(step)}</ol>
          {lines.some(l => l.group === group && l.tools && l.running) && <ol>{lines.filter(l => l.group === group && l.tools && l.running).map(step)}</ol>}
          {lines.some(l => l.group === group && l.tools && !l.running) && <details className="mt-2 text-sm"><summary>Tool checks ({lines.filter(l => l.group === group && l.tools && !l.running).length})</summary>
            <ol>{lines.filter(l => l.group === group && l.tools && !l.running).map(step)}</ol></details>}
        </section>)}</div>
        <ol><li ref={bottom} /></ol>
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

export function toolSentence(tool: string, running = false): string {
  const name = ({get_active_alerts: 'NWS alerts for the facility', get_facility_alerts: 'NWS alerts for the facility',
    fetch_alerts_for_facility: 'NWS alerts for the facility', fetch_commute_window_forecast: 'forecast wind and weather for the target commute',
    get_wind_forecast: 'forecast wind and gusts', get_hourly_forecast: 'hourly weather for the target day',
    get_point: 'the facility weather forecast location', check_commute_routes: 'employee commute routes',
    check_access_routes: 'nearby facility access routes', check_river: 'river conditions for the target day',
    check_local_closures: 'local closures', recheck_now: 'all evidence for the saved target day'} as Record<string, string>)[tool] ?? tool.replace(/^(?:get|fetch|check)_/, '').replaceAll('_', ' ');
  return `${running ? 'Checking' : 'Checked'} ${name}`;
}

function plainSummary(text: string) {
  if (/^\s*[\[{]/.test(text)) {
    try { const value = JSON.parse(text); return Array.isArray(value) ? `${value.length} source items examined.` : 'Source response checked; full details are in the saved evidence.'; } catch { /* ordinary citation prose */ }
  }
  return humanText(text);
}

function describe(e: RecordEvent): Line | null {
  const p = e.payload as Record<string, any>;
  const who = AGENT_LABEL[p.agent] ?? p.agent ?? "Agent";
  const base = { id: e.id, time: e.timestamp };
  switch (e.event_type) {
    case "investigation_requested":
      return { ...base, title: `Investigation for ${p.operating_date}`, detail: p.question, tone: "agent" };
    case "investigation.answer":
      return { ...base, title: p.source_label, detail: p.answer, tone: "agent" };
    case "investigation.evidence_added":
      return { ...base, title: `${p.evidence_ids.length} evidence rows added`, tone: "data" };
    case "recommendation_revised":
      return { ...base, title: "Recommendation revised", detail: Object.keys(p.diff.changes).join(', ') || 'No change to posture or recipients', tone: "engine" };
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
      return { ...base, title: toolSentence(p.tool, true), tone: "tool" };
    case "investigation.refresh_started":
      return { ...base, title: 'Refreshing expired sources', detail: (p.kinds ?? []).join(', '), tone: 'tool' };
    case "investigation.refresh_finished":
      return { ...base, title: 'Source refresh complete', detail: `${p.refreshed} refreshed; ${p.stale} still stale after refetch`, tone: 'tool' };
    case "investigation.refresh_failed":
      return { ...base, title: 'Source refresh failed', detail: `${p.tool}: ${p.reason}`, tone: 'muted' };
    case "agent.tool_call_end":
      return { ...base, title: toolSentence(p.tool), detail: p.summary, tone: "tool" };
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
      if (p.kind === "local_fact") return null; // shown as the agent's result
      return {
        ...base, title: `${p.kind === "local_feed" ? "Source" : "NWS"} ${String(p.kind === "local_feed" ? p.relevance_note : p.kind).replace(/_/g, " ").slice(0, 90)}: ${p.status}`,
        tone: p.status === "ok" ? "data" : "warn",
      };
    case "agent_unavailable":
      return { ...base, title: `${who} unavailable → ${p.fallback}`, detail: p.reason, tone: "warn" };
    case "writer_accepted":
      return { ...base, title: "Writer agent: rationale drafted", detail: `${p.words} words, validated`, tone: "agent" };
    case "writer_rejected":
      return { ...base, title: "Writer draft rejected → template", detail: p.reason, tone: "warn" };
    case "recommendation.created":
      return {
        ...base, title: `Rules engine: ${postureLabel(p.posture)}${p.status === "preliminary" ? " (preliminary)" : ""}`,
        detail: `${p.confidence} confidence · ${p.triggers.length} trigger(s) · rationale: ${p.rationale_source}`, tone: "engine",
      };
    case "decision.recorded":
      return { ...base, title: `Decision: ${p.action} by ${p.manager_name} → ${p.final_posture}`, tone: "engine" };
    case "dispatch_blocked":
      return { ...base, title: "Dispatch blocked: no approval", detail: `${p.tool} cancelled`, tone: "block" };
    case "local.coordinator":
      return {
        ...base, title: `Local coordinator chose: ${(p.agents as string[]).join(", ")}${p.by === "rules" ? " (rules)" : ""}`,
        detail: `${p.reason}${p.dropped_for_budget?.length ? ` · skipped for budget: ${p.dropped_for_budget.join(", ")}` : ""}`,
        tone: "agent",
      };
    case "local.prefilter":
      return { ...base, title: `${who}: ${p.relevant} of ${p.items} items pass the keyword filter`, tone: "muted" };
    case "local.agent_result":
      return {
        ...base, title: `${who}: ${p.facts} fact(s)${p.extracted_by === "code" ? " (code extraction)" : ""}`,
        detail: [...(p.summary ?? []), ...((p.rejected ?? []) as { reason: string }[]).map((r) => `rejected: ${r.reason}`)].join(" · ") || undefined,
        tone: p.rejected?.length ? "warn" : "agent",
      };
    case "local.roads_reused":
      return { ...base, title: `Roads agent: ${p.note}`, detail: `${p.facts} fact(s), no model call`, tone: "agent" };
    case "recommendation.finalized":
      return {
        ...base, title: `Final after road check: ${String(p.final.posture).replace("_", " ").toUpperCase()}`,
        detail: p.changed ? `changed from preliminary ${p.preliminary.posture} / ${p.preliminary.confidence}` : "same as preliminary",
        tone: "engine",
      };
    case "rationale.updated":
      return { ...base, title: `Rationale: ${p.rationale_note}`, tone: p.rationale_source === "agent" ? "agent" : "warn" };
    case "notice.created":
      return {
        ...base, title: `Notice v${p.version} prepared (${p.kind})`,
        detail: `${p.to_send} to send · ${p.unchanged} unchanged · ${p.decision_clock?.label ?? ""}`, tone: "engine",
      };
    case "messages.drafted":
      return {
        ...base, title: `Messages: ${p.agent_messages} by message agent, ${p.template_messages} template`,
        detail: `${p.groups} groups · ${p.drafted} drafted now · ${p.from_cache} reused${p.rejected?.length ? ` · ${p.rejected.length} rejected` : ""}`,
        tone: p.rejected?.length ? "warn" : "agent",
      };
    case "notification.sent":
      return {
        ...base, title: `Sent to ${p.name} (wave ${p.wave}, ${String(p.message_type).replace("_", " ")})`,
        detail: Object.entries(p.channels ?? {}).map(([ch, r]) => `${ch}${(r as { fallback?: boolean }).fallback ? " (fallback)" : ""}: ${(r as { ok: boolean }).ok ? "ok" : (r as { error?: string }).error}`).join(" · "),
        tone: "data",
      };
    case "notice.sent":
      return { ...base, title: `Notice sent: ${p.sent} messages`, detail: `${p.not_sent_unchanged} unchanged, not resent`, tone: "engine" };
    case "call_in.sent":
      return { ...base, title: `Call-in: ${p.name} for ${p.function}`, detail: `by ${p.manager_name} · ${p.coverage_before}`, tone: "engine" };
    case "ack.received":
      return { ...base, title: "Acknowledged", detail: `${p.seconds_after_send ?? "?"} s after send`, tone: "data" };
    case "ack.reminder_sent":
      return { ...base, title: "Ack reminder sent", tone: "warn" };
    case "ack.escalated":
      return { ...base, title: `No ack: escalated (level ${p.level})`, tone: "block" };
    default:
      return { ...base, title: e.event_type.replaceAll('.', ' — ').replaceAll('_', ' '), tone: "muted" };
  }
}
