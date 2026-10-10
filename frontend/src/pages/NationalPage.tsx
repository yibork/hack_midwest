import { useState } from "react";
import { Link } from "react-router-dom";
import { api, newEvaluationId, type NationalAlert, type RecommendationDetail } from "../api";
import AgentActivity from "../components/AgentActivity";
import RecommendationView from "../components/RecommendationView";
import { Button, Card, ErrorBox, Loading, fmtDateTime, useAsync } from "../components/ui";

const SEVERITY_STYLE: Record<string, string> = {
  Extreme: "bg-red-700 text-white",
  Severe: "bg-orange-500 text-white",
  Moderate: "bg-yellow-300 text-slate-900",
  Minor: "bg-slate-200 text-slate-800",
};

export default function NationalPage() {
  const scan = useAsync(() => api.nationalScan(), []);
  const [selected, setSelected] = useState<NationalAlert | null>(null);
  const [rec, setRec] = useState<RecommendationDetail | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [evalId, setEvalId] = useState<string | null>(null);

  async function evaluate(a: NationalAlert) {
    if (a.lat == null || a.lon == null) return;
    setSelected(a);
    setRec(null);
    setError(null);
    setEvaluating(true);
    const eid = newEvaluationId();
    setEvalId(eid);
    try {
      const name = `${a.event} – ${(a.area_desc ?? "").split(";")[0]}`;
      setRec(await api.evaluatePoint(a.lat, a.lon, name, eid));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setEvaluating(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">National scan</h1>
          <p className="text-sm text-slate-600">Top active NWS alerts by severity and urgency. Click a row to evaluate that location as a temporary facility.</p>
        </div>
        <Button variant="secondary" onClick={scan.reload} disabled={scan.loading}>Refresh</Button>
      </div>

      <Card>
        {scan.loading && <Loading label="Fetching active US alerts from api.weather.gov…" />}
        {scan.error && <ErrorBox error={scan.error} onRetry={scan.reload} />}
        {scan.data?.length === 0 && <p className="text-slate-600">No active alerts.</p>}
        {scan.data && scan.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500">
                <tr>
                  <th className="py-1 pr-3">Event</th>
                  <th className="py-1 pr-3">Area</th>
                  <th className="py-1 pr-3">Severity</th>
                  <th className="py-1 pr-3">Urgency</th>
                  <th className="py-1 pr-3">Certainty</th>
                  <th className="py-1">Onset</th>
                </tr>
              </thead>
              <tbody>
                {scan.data.map((a) => {
                  const clickable = a.lat != null;
                  return (
                    <tr key={a.id} onClick={() => clickable && evaluate(a)}
                        title={clickable ? "Evaluate this location" : "No location available for this alert"}
                        className={`border-t border-slate-100 align-top ${clickable ? "cursor-pointer hover:bg-slate-50" : "opacity-60"} ${selected?.id === a.id ? "bg-yellow-50" : ""}`}>
                      <td className="py-2 pr-3 font-semibold">{a.event}</td>
                      <td className="max-w-sm py-2 pr-3 text-slate-700">{a.area_desc}</td>
                      <td className="py-2 pr-3">
                        <span className={`rounded px-2 py-0.5 text-xs font-bold ${SEVERITY_STYLE[a.severity] ?? "bg-slate-100"}`}>{a.severity}</span>
                      </td>
                      <td className="py-2 pr-3">{a.urgency}</td>
                      <td className="py-2 pr-3">{a.certainty}</td>
                      <td className="py-2 whitespace-nowrap">{fmtDateTime(a.onset)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selected && (
        <div className="space-y-2">
          <h2 className="text-xl font-bold">{selected.event}: {selected.area_desc}</h2>
          <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
            <div className="min-w-0 space-y-2">
              {evaluating && <Loading label="Evaluating location against live NWS data…" />}
              {error && <ErrorBox error={error} onRetry={() => evaluate(selected)} />}
              {rec && (
                <RecommendationView
                  rec={rec}
                  actions={<Link to={`/decide?recommendation=${rec.id}`}><Button>Approve or override →</Button></Link>}
                />
              )}
            </div>
            <AgentActivity evaluationId={evalId} live={evaluating} />
          </div>
        </div>
      )}
    </div>
  );
}
