import type { Evidence, LocalFactValue } from "../api";
import { Card, fmtDateTime } from "./ui";

const AUTHORITY: Record<string, { label: string; style: string }> = {
  official_federal: { label: "Official - federal", style: "bg-emerald-100 text-emerald-900" },
  official_state: { label: "Official - state DOT", style: "bg-emerald-100 text-emerald-900" },
  official_local: { label: "Official - local", style: "bg-sky-100 text-sky-900" },
  news: { label: "Local news - unverified", style: "bg-amber-100 text-amber-900" },
};
const AGENT_LABEL: Record<string, string> = { roads: "Roads", schools: "Schools", public_safety: "Public safety", news: "News" };

export const isLocal = (e: Evidence) => e.source === "local";
const fact = (e: Evidence) => (e.kind === "local_fact" ? (e.value as LocalFactValue) : null);
const words = (s: string) => s.replace(/_/g, " ");

export function AuthorityBadge({ authority }: { authority: string }) {
  const a = AUTHORITY[authority] ?? { label: authority, style: "bg-slate-100" };
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold normal-case ${a.style}`}>{a.label}</span>;
}

/** In the evidence table: headline linked to the source, published time, supporting quote. */
export function LocalEvidenceDetail({ e }: { e: Evidence }) {
  const f = fact(e);
  if (f) {
    return (
      <div className="my-1 text-sm">
        <a className="underline" href={f.link} target="_blank" rel="noreferrer">{f.title}</a>
        <div className="text-xs text-slate-500">
          {f.source_name} · {f.reported_at ? `reported ${fmtDateTime(f.reported_at, "America/Chicago")}` : "undated"}
          {f.extracted_by === "code" ? " · extracted by code (model unavailable)" : ""}
        </div>
        <blockquote className="mt-1 border-l-2 border-slate-300 pl-2 text-xs italic text-slate-700">“{f.supporting_quote}”</blockquote>
      </div>
    );
  }
  if (e.kind === "local_feed") {
    const v = e.value as { source_name: string; item_count: number; fetch_status: string };
    return <div className="text-xs text-slate-600">{v.source_name}: {v.fetch_status}, {v.item_count} item(s)</div>;
  }
  return null;
}

function summarize(agent: string, facts: LocalFactValue[]): string {
  const byType = new Map<string, LocalFactValue[]>();
  for (const f of facts) byType.set(f.fact_type, [...(byType.get(f.fact_type) ?? []), f]);
  return [...byType.entries()].map(([type, list]) => {
    const access = [...new Set(list.filter((f) => f.matches_facility_access_route).flatMap((f) => f.routes_mentioned))];
    const base = `${list.length} ${words(type)}${list.length > 1 ? "s" : ""}`;
    return access.length ? `${base} on access route ${access.join(", ")}` : base;
  }).join("; ") || `nothing relevant (${AGENT_LABEL[agent]})`;
}

/** Recommendation page: local signals grouped by agent, with counts and the facts. */
export default function LocalSignals({ evidence, tz }: { evidence: Evidence[]; tz: string }) {
  const facts = evidence.map(fact).filter((f): f is LocalFactValue => f !== null);
  const feeds = evidence.filter((e) => e.kind === "local_feed");
  if (!feeds.length && !facts.length) return null;
  const agents = [...new Set([...feeds.map((e) => (e.value as { agent: string }).agent), ...facts.map((f) => f.agent)])];
  const failed = feeds.filter((e) => e.status !== "ok");
  return (
    <Card title={`Local signals (${facts.length} fact${facts.length === 1 ? "" : "s"} from ${feeds.length} sources)`}>
      <p className="mb-3 text-xs text-slate-500">
        Agents extract facts from allowlisted sources; code checks each against the source text, and policy rules decide
        what they can change. Local signals never lower the posture. News is never treated as confirmation.
      </p>
      <div className="space-y-3">
        {agents.map((agent) => {
          const mine = facts.filter((f) => f.agent === agent);
          return (
            <div key={agent}>
              <div className="font-semibold">{AGENT_LABEL[agent] ?? agent}: <span className="font-normal">{summarize(agent, mine)}</span></div>
              <ul className="mt-1 space-y-1 text-sm">
                {mine.map((f) => (
                  <li key={f.item_id} className="flex flex-wrap items-baseline gap-2">
                    <AuthorityBadge authority={f.authority} />
                    <a className="underline" href={f.link} target="_blank" rel="noreferrer">{f.location_text || f.title}</a>
                    {f.matches_facility_access_route && <span className="rounded bg-red-100 px-1 text-xs font-semibold text-red-800">facility access route</span>}
                    {f.matches_employee_commute_routes.length > 0 && (
                      <span className="rounded bg-amber-100 px-1 text-xs text-amber-900">commute: {f.matches_employee_commute_routes.join(", ")}</span>
                    )}
                    <span className="text-xs text-slate-500">{f.reported_at ? fmtDateTime(f.reported_at, tz) : "undated"} · {f.confidence}</span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
      {failed.length > 0 && (
        <p className="mt-3 text-sm text-amber-800">
          Unavailable (not treated as "no closures"): {failed.map((e) => (e.value as { source_name: string }).source_name).join(", ")}
        </p>
      )}
    </Card>
  );
}
