import { useState } from "react";
import SavedCopyLinks from "../components/SavedCopyLinks";
import RecordVerification from "../components/RecordVerification";
import { api, type RecordAt, type RecommendationDetail } from "../api";
import { Button, Card, ConfidenceBadge, ErrorBox, Loading, PolicyDetails, PostureBadge, StatusBadge, fmtDateTime, humanText, ruleLabel, useAsync } from "../components/ui";

const EVENT_COLOR: Record<string, string> = {
  "evidence.fetched": "border-sky-400",
  "recommendation.created": "border-purple-500",
  "decision.recorded": "border-green-600",
  "notification.sent": "border-orange-500",
  "notification.skipped": "border-slate-300",
};

/** Current local time formatted for <input type="datetime-local">. */
function nowLocalInput(): string {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export default function RecordPage() {
  const facilities = useAsync(() => api.facilities(), []);
  const [facilityId, setFacilityId] = useState<string>("");
  const fid = facilityId || facilities.data?.[0]?.id || "";

  const events = useAsync(() => (fid ? api.record({ facility_id: fid }) : Promise.resolve([])), [fid]);
  const current = useAsync(() => (fid ? api.latestRecommendation(fid) : Promise.resolve(null)), [fid]);

  const [when, setWhen] = useState(nowLocalInput());
  const [past, setPast] = useState<RecordAt | null>(null);
  const [pastLoading, setPastLoading] = useState(false);
  const [pastError, setPastError] = useState<string | null>(null);

  async function lookup() {
    setPastLoading(true);
    setPastError(null);
    try {
      setPast(await api.recordAt(fid, new Date(when).toISOString()));
    } catch (e) {
      setPastError(e instanceof Error ? e.message : String(e));
    } finally {
      setPastLoading(false);
    }
  }

  if (facilities.loading) return <Loading />;
  if (facilities.error) return <ErrorBox error={facilities.error} onRetry={facilities.reload} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Decision record</h1>
          <p className="text-sm text-slate-600">Append-only log of every evidence fetch, evaluation, decision and notification.</p>
        </div>
        <select className="rounded border border-slate-300 bg-white px-2 py-1" value={fid} onChange={(e) => setFacilityId(e.target.value)}>
          {facilities.data?.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      </div>

      {fid && <RecordVerification facilityId={fid} revision={events.data} />}
      <Card title="What was known at a moment">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="text-sm font-semibold">Time (your local time)</span>
            <input type="datetime-local" className="mt-1 block rounded border border-slate-300 px-3 py-2" value={when}
                   onChange={(e) => setWhen(e.target.value)} />
          </label>
          <Button onClick={lookup} disabled={!fid || pastLoading}>{pastLoading ? "Loading…" : "Show"}</Button>
        </div>
        {pastError && <div className="mt-3"><ErrorBox error={pastError} /></div>}
        {past && (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Snapshot title={`As of ${fmtDateTime(past.as_of)}`} rec={past.recommendation}
                      notificationCount={past.notifications.length} />
            <Snapshot title="Now" rec={current.data ?? null} />
          </div>
        )}
      </Card>

      <Card title={`Timeline (${events.data?.length ?? 0} events)`} actions={<Button variant="secondary" className="!py-1" onClick={events.reload}>Refresh</Button>}>
        {events.loading && <Loading />}
        {events.error && <ErrorBox error={events.error} onRetry={events.reload} />}
        {events.data?.length === 0 && <p className="text-slate-600">No events yet. Evaluate the facility to start the record.</p>}
        <ol className="space-y-1">
          {events.data?.map((e) => (
            <li key={e.id} className={`border-l-4 pl-3 ${EVENT_COLOR[e.event_type] ?? "border-slate-300"}`}>
              <details>
                <summary className="cursor-pointer py-1 text-sm">
                  <span className="mr-3 font-mono text-xs text-slate-500">#{e.id}</span>
                  <span className="mr-3 tabular-nums text-slate-600">{fmtDateTime(e.timestamp)}</span>
                  <span className="font-semibold">{humanText(e.event_type.replaceAll('.', ' — '))}</span>
                  {"status" in e.payload && <span className="ml-2"><StatusBadge status={e.payload.status as "ok"} /></span>}
                </summary>
                <details className="mb-2"><summary>Technical details</summary><pre className="overflow-x-auto rounded bg-slate-50 p-3 text-xs">{JSON.stringify(e.payload, null, 2)}</pre></details>
                {e.event_type === 'decision.recorded' && typeof e.payload.decision_id === 'string' && <p className="mb-2"><a className="underline" href={`/decisions/${e.payload.decision_id}/memo`} target="_blank" rel="noreferrer">Decision memo · Print / save PDF</a></p>}
              </details>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}

function Snapshot({ title, rec, notificationCount }: { title: string; rec: RecommendationDetail | null; notificationCount?: number }) {
  return (
    <div className="rounded border border-slate-200 p-3">
      <h3 className="mb-2 font-semibold">{title}</h3>
      {!rec ? (
        <p className="text-sm text-slate-500">No recommendation existed yet.</p>
      ) : (
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <PostureBadge posture={rec.posture} />
            <ConfidenceBadge confidence={rec.confidence} />
          </div>
          <div>Evaluated {fmtDateTime(rec.evaluated_at)} · <PolicyDetails version={rec.policy_version} /></div>
          <div>Triggers: {rec.triggers.length ? rec.triggers.map((t) => ruleLabel(t.rule_id)).join(", ") : "none"}</div>
          <details><summary>Saved evidence ({rec.evidence.length})</summary><div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr><th>Evidence</th><th>Retrieved</th><th>Copies</th></tr></thead><tbody>
            {rec.evidence.map(e => <tr key={e.id}><td>{e.scenario_kind === 'simulated' ? 'SIMULATED · ' : e.scenario_kind === 'replay' ? 'REPLAY · ' : ''}{humanText(e.kind)} <StatusBadge status={e.status} /></td><td>{fmtDateTime(e.retrieved_at)}</td><td><SavedCopyLinks evidence={e} /></td></tr>)}
          </tbody></table></div></details>
          <div>
            Decision:{" "}
            {rec.decision ? <>{rec.decision.action} by {rec.decision.manager_name} → <PostureBadge posture={rec.decision.final_posture} size="sm" /></> : "none yet"}
          </div>
          {notificationCount !== undefined && <div>Notifications sent by then: {notificationCount}</div>}
          {rec.decision && <a className="block underline" href={`/decisions/${rec.decision.id}/memo`} target="_blank" rel="noreferrer">Decision memo · Print / save PDF</a>}
        </div>
      )}
    </div>
  );
}
