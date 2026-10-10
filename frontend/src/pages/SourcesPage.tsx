import { api } from "../api";
import { AuthorityBadge } from "../components/LocalSignals";
import { Card, ErrorBox, Loading, fmtDateTime, useAsync } from "../components/ui";

const STATUS_STYLE: Record<string, string> = {
  ok: "bg-green-100 text-green-800", unknown: "bg-red-100 text-red-800", skipped: "bg-slate-200 text-slate-700",
};

/** The local-signals allowlist: what each agent may read, last fetch, and what was skipped and why. */
export default function SourcesPage() {
  const data = useAsync(() => api.sources(), []);
  if (data.loading) return <Loading />;
  if (data.error) return <ErrorBox error={data.error} onRetry={data.reload} />;
  const d = data.data!;
  const tz = "America/Chicago";
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Sources</h1>
      <p className="text-sm text-slate-600">
        Local agents read only these URLs (never links found inside them), respect robots.txt and each source's refresh
        interval, and keep at most a 300-character excerpt per item. A failed fetch is "unknown", never "no closures".
      </p>
      <Card title={`Local signal sources (${d.sources.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1 pr-3">Source</th><th className="py-1 pr-3">Agent</th><th className="py-1 pr-3">Authority</th>
                <th className="py-1 pr-3">Last fetch</th><th className="py-1 pr-3">Status</th><th className="py-1 pr-3">Items</th><th className="py-1">Notes</th></tr>
            </thead>
            <tbody>
              {d.sources.map((s) => (
                <tr key={s.id} className="border-t border-slate-100 align-top">
                  <td className="py-2 pr-3">
                    <a className="font-semibold underline" href={s.link ?? s.url} target="_blank" rel="noreferrer">{s.name}</a>
                    <div className="break-all font-mono text-[11px] text-slate-500">{s.kind} · {s.url}</div>
                    <div className="text-xs text-slate-500">every {s.refresh_minutes} min · {(s.area.counties ?? []).join(", ")}</div>
                  </td>
                  <td className="py-2 pr-3">{s.agent.replace("_", " ")}</td>
                  <td className="py-2 pr-3"><AuthorityBadge authority={s.authority} /></td>
                  <td className="py-2 pr-3 whitespace-nowrap">{fmtDateTime(s.last_fetch, tz)}</td>
                  <td className="py-2 pr-3"><span className={`rounded px-2 py-0.5 text-xs font-bold ${STATUS_STYLE[s.status] ?? "bg-slate-100"}`}>{s.status}</span>
                    {s.error && <div className="text-xs text-red-700">{s.error}</div>}</td>
                  <td className="py-2 pr-3">{s.items_found ?? "—"}</td>
                  <td className="py-2 text-xs text-slate-600">{s.robots}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={`Already integrated (${d.integrated.length})`}>
        <ul className="space-y-1 text-sm">
          {d.integrated.map((s) => (
            <li key={s.id}><AuthorityBadge authority={s.authority} /> {s.name} <span className="font-mono text-xs text-slate-500">{s.url}</span>
              {s.note && <div className="text-xs text-amber-800">{s.note}</div>}</li>
          ))}
        </ul>
      </Card>
      <Card title={`Skipped (${d.skipped.length})`}>
        <ul className="space-y-2 text-sm">
          {d.skipped.map((s) => (
            <li key={s.name}><strong>{s.name}</strong>{s.url && <span className="ml-1 break-all font-mono text-xs text-slate-500">{s.url}</span>}
              <div className="text-slate-600">{s.reason}</div></li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
