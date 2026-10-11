import type { Evidence, LocalFactValue } from "../api";
import { Card, fmtDateTime } from "./ui";

const AUTHORITY: Record<string, { label: string; style: string }> = {
  official_federal: { label: "Official - federal", style: "bg-emerald-100 text-emerald-900" },
  official_state: { label: "Official - state DOT", style: "bg-emerald-100 text-emerald-900" },
  official_local: { label: "Official - local", style: "bg-sky-100 text-sky-900" },
  news: { label: "Local news - unverified", style: "bg-amber-100 text-amber-900" },
};
const AGENT_LABEL: Record<string, string> = { roads: "Roads", schools: "Schools", public_safety: "Public safety", news: "News" };

export const isLocal = (e: Evidence) => e.source === "local" || e.kind === "local_fact";
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
          <Where f={f} /> · <Since f={f} /> · {f.source_name}
          {f.extracted_by === "code" ? " · extracted by code (model unavailable)" : ""}
        </div>
        <blockquote className="mt-1 border-l-2 border-slate-300 pl-2 text-xs italic text-slate-700">“{f.supporting_quote}”</blockquote>
      </div>
    );
  }
  if (e.kind === "local_feed") {
    return <div className="text-xs text-slate-600">{e.relevance_note}</div>;
  }
  return null;
}

/** "construction, active since Mar 1, 2025": road items are current conditions, often long-running. */
function Since({ f }: { f: LocalFactValue }) {
  const since = f.active_since ?? f.reported_at;
  const what = f.category && f.category !== "other" ? f.category : null;
  if (!since) return <>{what ?? "undated"}</>;
  const d = new Date(since).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  return <>{what ? `${what}, ` : ""}active since {d}</>;
}

/** "31 km from facility (source coordinates)", or why location is uncertain. */
function Where({ f }: { f: LocalFactValue }) {
  if (f.relevance === "uncertain" || f.distance_km == null) {
    return <span className="font-semibold text-amber-800" title={f.location_note ?? ""}>location uncertain (no coordinates): not used by rules</span>;
  }
  const how = f.location_provenance === "geocoded_text" ? "geocoded, approximate" : "source coordinates";
  return <span title={f.location_note ?? ""}>{f.distance_km} km from facility ({how})</span>;
}

function summarize(agent: string, facts: LocalFactValue[]): string {
  const byType = new Map<string, LocalFactValue[]>();
  for (const f of facts) byType.set(f.fact_type, [...(byType.get(f.fact_type) ?? []), f]);
  return [...byType.entries()].map(([type, list]) => {
    const access = [...new Set(list.filter((f) => f.relevance === "facility").flatMap((f) => f.routes_mentioned))];
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
  const results = evidence.filter((e) => e.kind === "local_agent_result")
    .map((e) => e.value as { agent: string; rejected: { reason: string; title: string }[] });
  return (
    <Card title={`Local signals (${facts.length} fact${facts.length === 1 ? "" : "s"} from ${feeds.length} sources)`}>
      <ul className="mb-2 text-xs text-slate-500">
        {feeds.map((e) => <li key={e.id}>{e.relevance_note}</li>)}
      </ul>
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
                    <AuthorityBadge authority={(f as LocalFactValue & {simulated?: boolean}).simulated ? "SIMULATED" : f.authority} />
                    <a className="underline" href={f.link} target="_blank" rel="noreferrer">{f.location_text || f.title}</a>
                    {f.matches_facility_access_route && <span className="rounded bg-red-100 px-1 text-xs font-semibold text-red-800">on access segment {f.segments.join(", ")}</span>}
                    <span className="text-xs text-slate-600"><Where f={f} /></span>
                    {f.relevance === "commute" && (
                      <span className="rounded bg-amber-100 px-1 text-xs font-semibold text-amber-900">
                        affects {f.affected_employee_ids.length} commute{f.affected_employee_ids.length === 1 ? "" : "s"} ({f.matches_employee_commute_routes.join(", ")})
                      </span>
                    )}
                    <span className="text-xs text-slate-500"><Since f={f} /> · {f.confidence}</span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
      {results.filter((r) => r.rejected.length).map((r) => (
        <div key={r.agent} className="mt-3 text-sm text-slate-700">
          <span className="font-semibold">{AGENT_LABEL[r.agent] ?? r.agent}: {r.rejected.length} fact{r.rejected.length > 1 ? "s" : ""} rejected</span>
          <ul className="ml-4 list-disc text-xs text-slate-600">
            {r.rejected.map((x, i) => <li key={i}>{x.reason}{x.title ? ` ("${x.title}")` : ""}</li>)}
          </ul>
        </div>
      ))}
      {failed.length > 0 && (
        <p className="mt-3 text-sm text-amber-800">
          Unavailable (not treated as "no closures"): {failed.map((e) => (e.value as { source_name: string }).source_name).join(", ")}
        </p>
      )}
    </Card>
  );
}
