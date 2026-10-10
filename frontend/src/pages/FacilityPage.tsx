import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, newEvaluationId, type RecommendationDetail } from "../api";
import AgentActivity from "../components/AgentActivity";
import RecommendationView from "../components/RecommendationView";
import { Button, ErrorBox, Loading, useAsync } from "../components/ui";

export default function FacilityPage() {
  const [params, setParams] = useSearchParams();
  const facilities = useAsync(() => api.facilities(), []);
  const facilityId = params.get("facility") ?? facilities.data?.[0]?.id;
  const facility = facilities.data?.find((f) => f.id === facilityId);

  const latest = useAsync<RecommendationDetail | null>(
    () => (facilityId ? api.latestRecommendation(facilityId) : Promise.resolve(null)),
    [facilityId],
  );
  const [evaluating, setEvaluating] = useState(false);
  const [evalError, setEvalError] = useState<string | null>(null);
  const [runningEval, setRunningEval] = useState<string | null>(null);

  async function reevaluate() {
    if (!facilityId) return;
    const eid = newEvaluationId();
    setRunningEval(eid);
    setEvaluating(true);
    setEvalError(null);
    try {
      latest.setData(await api.evaluate(facilityId, eid));
    } catch (e) {
      setEvalError(e instanceof Error ? e.message : String(e));
    } finally {
      setEvaluating(false);
    }
  }

  if (facilities.loading) return <Loading />;
  if (facilities.error) return <ErrorBox error={facilities.error} onRetry={facilities.reload} />;

  const rec = latest.data;
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
      <div className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            {facilities.data && facilities.data.length > 1 ? (
              <select
                className="rounded border border-slate-300 bg-white px-2 py-1 text-lg font-bold"
                value={facilityId}
                onChange={(e) => setParams({ facility: e.target.value })}
              >
                {facilities.data.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
              </select>
            ) : (
              <h1 className="text-2xl font-bold">{facility?.name}</h1>
            )}
            {facility && (
              <div className="text-sm text-slate-600">
                {facility.address} · Open {facility.open_time}–{facility.close_time} ({facility.timezone}) · Routes{" "}
                {facility.access_routes.join(", ")}
              </div>
            )}
          </div>
          <Button onClick={reevaluate} disabled={evaluating || !facilityId}>
            {evaluating ? "Evaluating… (live NWS)" : "Re-evaluate"}
          </Button>
        </div>

        {evalError && <ErrorBox error={evalError} onRetry={reevaluate} />}
        {latest.loading && !rec && <Loading />}
        {latest.error && <ErrorBox error={latest.error} onRetry={latest.reload} />}
        {!latest.loading && !latest.error && !rec && (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
            <p className="mb-4 text-slate-600">No evaluation yet for this facility.</p>
            <Button onClick={reevaluate} disabled={evaluating}>{evaluating ? "Evaluating…" : "Evaluate now"}</Button>
          </div>
        )}
        {rec && (
          <RecommendationView
            rec={rec}
            actions={
              <Link to={`/decide?recommendation=${rec.id}`}>
                <Button variant={rec.decision ? "secondary" : "primary"}>
                  {rec.decision ? "View decision & notifications" : "Approve or override →"}
                </Button>
              </Link>
            }
          />
        )}
      </div>
      <AgentActivity evaluationId={evaluating ? runningEval : rec?.evaluation_id ?? runningEval} live={evaluating} />
    </div>
  );
}
