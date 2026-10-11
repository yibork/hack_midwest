import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api";
import { Card, ErrorBox, Loading, useAsync } from "../components/ui";

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
    <h1 className="text-2xl font-bold">Thresholds · policy {policy.data.version}</h1>
    <p>Sources define hazards and employment obligations. Every hazard-to-posture mapping is a choice of Facility policy v1.0 (this system), subject to manager approval.</p>
    <Card title="Change log">{policy.data.change_log.map(c => <p key={c.version}>{c.date} · {c.version}: {c.changes}</p>)}</Card>
    {([['federal', 'Federal standards and source definitions (NWS / NOAA / OPM / CAP)'], ['coordination', 'Local coordination (FEB)'], ['facility', 'Facility policy choices — not federal standards']] as const).map(([group, label]) =>
      <Card key={group} title={label}><div className="overflow-x-auto"><table className="w-full text-sm">
        <thead><tr className="text-left">{['Rule', 'Condition', 'Posture', 'Source / section', 'Verified', 'Justification', 'Last verified'].map(h => <th className="p-2" key={h}>{h}</th>)}</tr></thead>
        <tbody>{policy.data!.rules.filter(r => r.group === group).map(r => <tr id={r.id} key={r.id} className="scroll-mt-4 border-t border-slate-200 align-top target:bg-yellow-50">
          <td className="p-2"><strong>{r.id}</strong><p>{r.description}</p></td>
          <td className="p-2">{typeof r.condition === 'string' ? r.condition : r.condition.any.map(c => Object.entries(c).map(([k,v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join('; ')).join(' OR ')}</td>
          <td className="p-2">{r.posture.replace(/_/g, ' ')}</td>
          <td className="p-2"><a className="underline" href={r.citation.url} target={r.citation.url.startsWith('http') ? '_blank' : undefined} rel="noreferrer">{r.citation.title}</a><p>{r.citation.publisher}</p><p>{r.citation.section_or_page}</p></td>
          <td className="p-2"><span className={`rounded px-2 py-1 ${r.verified ? 'bg-green-100 text-green-900' : 'bg-amber-100 text-amber-900'}`}>{r.verified ? 'Verified' : 'Unverified'}</span></td>
          <td className="p-2">{r.justification}</td><td className="p-2">{r.verified ? r.citation.retrieved_at : 'Not verified'}</td>
        </tr>)}</tbody>
      </table></div></Card>)}
  </div>;
}
