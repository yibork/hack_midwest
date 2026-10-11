import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api";
import { Card, ErrorBox, Loading, PolicyDetails, humanText, postureLabel, ruleLabel, useAsync } from "../components/ui";

function conditionText(condition: string | {any: Record<string, unknown>[]}) {
  if (typeof condition === "string") return humanText(condition);
  return condition.any.map(c => c.type === "forecast_gust" ? `Forecast gusts at least ${c.min_mph} mph${c.below_mph ? `, below ${c.below_mph} mph` : ""} during the commute`
    : c.type === "forecast_wind" ? `Sustained wind at least ${c.min_mph} mph for ${c.duration_minutes} minutes during the commute`
    : `${Array.isArray(c.events) ? c.events.join(" or ") : `Alert severity ${Array.isArray(c.severity) ? c.severity.join(" / ") : "any"}; urgency ${Array.isArray(c.urgency) ? c.urgency.join(" / ") : "any"}`} during ${String(c.window ?? "operating day").replaceAll("_", " ")}`).join("; or ");
}

export default function ThresholdsPage() {
  const policy = useAsync(() => api.thresholds(), []);
  const location = useLocation();
  useEffect(() => {
    if (location.hash && policy.data) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }, [location.hash, policy.data]);
  if (policy.loading) return <Loading />;
  if (policy.error) return <ErrorBox error={policy.error} />;
  if (!policy.data) return null;
  return <div className="space-y-5">
    <h1 className="text-2xl font-bold">Thresholds</h1>
    <PolicyDetails version={policy.data.version} />
    <p>Sources define hazards and employment obligations. Every hazard-to-posture mapping is a choice of Facility policy v1.0 (this system), subject to manager approval.</p>
    <Card title="Change log">{policy.data.change_log.map(c => <p key={c.version}>{c.date} · {c.version}: {c.changes}</p>)}</Card>
    {([['federal', 'Federal standards and source definitions (NWS / NOAA / OPM / CAP)'], ['coordination', 'Local coordination (FEB)'], ['facility', 'Facility policy choices — not federal standards']] as const).map(([group, label]) =>
      <Card key={group} title={label}><div className="overflow-x-auto"><table className="w-full text-sm">
        <thead><tr className="text-left">{['Rule', 'Condition', 'Posture', 'Source / section', 'Verified', 'Justification', 'Last verified'].map(h => <th className="p-2" key={h}>{h}</th>)}</tr></thead>
        <tbody>{policy.data!.rules.filter(r => r.group === group).map(r => <tr id={r.id} key={r.id} className="scroll-mt-4 border-t border-slate-200 align-top target:bg-yellow-50">
          <td className="p-2"><strong>{ruleLabel(r.id)}</strong><p>{humanText(r.description)}</p><details><summary>Technical details</summary><code>{r.id}</code></details></td>
          <td className="p-2">{conditionText(r.condition as string | {any: Record<string, unknown>[]})}</td>
          <td className="p-2">{r.posture in {normal: 1, closure: 1, delayed_arrival: 1, early_dismissal: 1, telework: 1} ? postureLabel(r.posture as Parameters<typeof postureLabel>[0]) : humanText(r.posture)}</td>
          <td className="p-2"><a className="underline" href={r.citation.url} target={r.citation.url.startsWith('http') ? '_blank' : undefined} rel="noreferrer">{r.citation.title}</a><p>{r.citation.publisher}</p><p>{r.citation.section_or_page}</p></td>
          <td className="p-2"><span className={`rounded px-2 py-1 ${r.verified ? 'bg-green-100 text-green-900' : 'bg-amber-100 text-amber-900'}`}>{r.verified ? 'Verified' : 'Unverified'}</span></td>
          <td className="p-2">{r.justification}</td><td className="p-2">{r.verified ? r.citation.retrieved_at : 'Not verified'}</td>
        </tr>)}</tbody>
      </table></div></Card>)}
  </div>;
}
