import { useEffect, useState } from "react";
import { api, type EmployeePublic } from "../api";
import { useViewer } from "../components/RoleContext";
import { Card, ErrorBox, Loading, PostureBadge, fmtTime, useAsync } from "../components/ui";

/** Supervisor: own team only (the server filters). Managers can look at any supervisor's board. */
export default function TeamPage() {
  const { viewer } = useViewer();
  const isManager = viewer.role === "facility_manager";
  const [supervisors, setSupervisors] = useState<EmployeePublic[]>([]);
  const [pick, setPick] = useState<string | undefined>(undefined);
  useEffect(() => {
    if (isManager) api.viewers().then((v) => {
      const sups = v.supervisors.filter((s) => s.role === "supervisor");
      setSupervisors(sups);
      setPick((p) => p ?? sups[0]?.id);
    }).catch(() => {});
  }, [isManager]);
  const board = useAsync(() => (isManager && !pick ? Promise.resolve(null) : api.team(isManager ? pick : undefined)), [pick, isManager]);

  if (board.loading && !board.data) return <Loading />;
  if (board.error) return <ErrorBox error={board.error} onRetry={board.reload} />;
  const b = board.data;
  if (!b) return null;
  const tz = "America/Chicago";
  const acks = b.notifications.filter((n) => n.requires_ack);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">Team · {b.supervisor.name}</h1>
        {b.notice && <PostureBadge posture={b.notice.posture} />}
        {isManager && (
          <select className="rounded border border-slate-300 bg-white px-2 py-1" value={pick} onChange={(e) => setPick(e.target.value)}
                  aria-label="Supervisor">
            {supervisors.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
      </div>
      <p className="text-sm text-slate-600">
        {b.notice ? `Notice v${b.notice.version} for ${b.notice.operating_date}${b.notice.sent_at ? `, sent ${fmtTime(b.notice.sent_at, tz)}` : " (not sent yet)"}.`
          : "No decision yet: showing what the latest recommendation would mean for your team."}{" "}
        {b.members.length} people · {acks.filter((n) => n.acknowledged_at).length}/{acks.length} acknowledgments.
      </p>

      {b.suggestions.length > 0 && (
        <Card title={`Commute exposure (${b.suggestions.length})`}>
          <ul className="space-y-2 text-sm">
            {b.suggestions.map((s) => (
              <li key={s.employee_id} className="rounded border border-amber-300 bg-amber-50 p-2">
                <strong>{s.name}</strong>: {s.audience_text}.<div>Suggestion: {s.suggestion}.</div>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-slate-500">The system never tells anyone to stay home; the decision is yours, under agency policy.</p>
        </Card>
      )}

      <Card title="Team board">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr><th className="py-1 pr-3">Person</th><th className="py-1 pr-3">Instruction</th><th className="py-1 pr-3">Access</th>
                <th className="py-1 pr-3">Wave</th><th className="py-1 pr-3">Notify by</th><th className="py-1 pr-3">Delivery</th><th className="py-1">Ack</th></tr>
            </thead>
            <tbody>
              {b.notifications.map((n) => (
                <tr key={n.notification_id} className={`border-t border-slate-100 align-top ${n.included ? "" : "text-slate-500"}`}>
                  <td className="py-2 pr-3"><div className="font-semibold">{n.name}</div><div className="text-xs text-slate-500">{n.audience_text}</div></td>
                  <td className="py-2 pr-3">{n.duty_instruction}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{n.building_access}</td>
                  <td className="py-2 pr-3">{n.included ? n.wave : "—"}</td>
                  <td className="py-2 pr-3 whitespace-nowrap">{fmtTime(n.notify_by, tz)}</td>
                  <td className="py-2 pr-3">{!n.included ? "not sent" : n.delivered == null ? "pending" : n.delivered ? "✓ delivered" : "✗ not delivered"}</td>
                  <td className="py-2 whitespace-nowrap">
                    {!n.requires_ack ? "—" : n.acknowledged_at ? <span className="text-green-700">✓ {fmtTime(n.acknowledged_at, tz)}</span>
                      : n.escalation_level ? <span className="font-semibold text-red-700">escalated ({n.escalation_level})</span> : "waiting"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
