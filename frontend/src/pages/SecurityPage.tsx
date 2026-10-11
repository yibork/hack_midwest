import { useState } from "react";
import { api } from "../api";
import { Card, ErrorBox, Loading, PostureBadge, humanText, useAsync } from "../components/ui";

const ACCESS_STYLE: Record<string, string> = {
  allowed: "bg-green-100 text-green-900",
  allowed_with_escort: "bg-sky-100 text-sky-900",
  allowed_after: "bg-amber-100 text-amber-900",
  allowed_until: "bg-amber-100 text-amber-900",
  denied: "bg-red-100 text-red-900",
};

/** Security desk: who may enter today, with the time window and the reason code. */
export default function SecurityPage() {
  const facilities = useAsync(() => api.facilities(), []);
  const facilityId = facilities.data?.[0]?.id;
  const list = useAsync(() => (facilityId ? api.accessList(facilityId) : Promise.resolve(null)), [facilityId]);
  const [query, setQuery] = useState("");

  if (facilities.loading || list.loading) return <Loading />;
  if (facilities.error) return <ErrorBox error={facilities.error} />;
  if (list.error) return <ErrorBox error={list.error} onRetry={list.reload} />;
  const data = list.data;
  if (!data) return null;
  const rows = data.rows.filter((r) => !query || r.name.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">Building access · {data.facility_name}</h1>
        <PostureBadge posture={data.posture} />
        <span className="text-slate-600">{data.operating_date}</span>
      </div>
      <p className="text-sm text-slate-600">
        {data.notice_id ? "From the latest approved notice." : "No decision yet today: normal access."} Policy: {data.policy}
      </p>
      <Card title={`Access list (${data.rows.length})`}
            actions={<input className="rounded border border-slate-300 px-2 py-1 text-sm" placeholder="Find a name"
                            aria-label="Find a name" value={query} onChange={(e) => setQuery(e.target.value)} />}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1 pr-3">Name</th><th className="py-1 pr-3">Access</th><th className="py-1 pr-3">Window</th>
                <th className="py-1 pr-3">Badge</th><th className="py-1 pr-3">Scheduled</th><th className="py-1">Reason</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.employee_id} className="border-t border-slate-100 align-top">
                  <td className="py-2 pr-3 font-semibold">{r.name}<div className="text-xs font-normal text-slate-500">{r.role.replace("_", " ")}</div></td>
                  <td className="py-2 pr-3">
                    <span className={`rounded px-2 py-0.5 text-xs font-bold ${ACCESS_STYLE[r.access.split(" ")[0]] ?? ""}`}>{humanText(r.access)}</span>
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap">{r.window}</td>
                  <td className="py-2 pr-3">{r.access_level.replace("_", " ")}</td>
                  <td className="py-2 pr-3">{r.scheduled.replace("_", " ")}</td>
                  <td className="py-2"><div className="text-xs text-slate-500">{humanText(r.reason)}</div><details><summary>Technical details</summary><code>{r.code}</code></details></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
