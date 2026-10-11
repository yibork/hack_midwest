import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, newEvaluationId, type EvaluateTarget, type RecommendationDetail } from "../api";
import AgentActivity from "../components/AgentActivity";
import RecommendationView from "../components/RecommendationView";
import InvestigationPanel from "../components/InvestigationPanel";
import DemoScenario from "../components/DemoScenario";
import { Button, ErrorBox, Loading, useAsync } from "../components/ui";

export default function FacilityPage() {
  const [params, setParams] = useSearchParams();
  const facilities = useAsync(() => api.facilities(), []);
  const facilityId = params.get("facility") ?? facilities.data?.[0]?.id;
  const selectedFacility = facilities.data?.find((f) => f.id === facilityId);

  const latest = useAsync<RecommendationDetail | null>(
    () => (facilityId ? api.latestRecommendation(facilityId) : Promise.resolve(null)),
    [facilityId],
  );
  const [evaluating, setEvaluating] = useState(false);
  const facility = selectedFacility ?? latest.data?.facility;
  const [evalError, setEvalError] = useState<string | null>(null);
  const [runningEval, setRunningEval] = useState<string | null>(null);
  const [target, setTarget] = useState<string>("next"); // "next" | "demo" | YYYY-MM-DD
  const health = useAsync(() => api.health(), []);
  const demo = !!health.data?.demo_mode;

  async function reevaluate() {
    if (!facilityId) return;
    const eid = newEvaluationId();
    setRunningEval(eid);
    setEvaluating(true);
    setEvalError(null);
    const opts: EvaluateTarget = target === "next" ? {} : target === "demo" ? { demo_workday: true } : { target_date: target };
    try {
      const scenario = await api.demoState();
      latest.setData(scenario.enabled && ["simulated_winter", "live_national"].includes(scenario.mode)
        ? await api.demoScenario(scenario.mode)
        : scenario.mode === "replay" && latest.data ? await api.recommendation(latest.data.id)
        : await api.evaluate(facilityId, eid, opts));
      window.dispatchEvent(new Event("demo-scenario-change"));
    } catch (e) {
      setEvalError(e instanceof Error ? e.message : String(e));
    } finally {
      setEvaluating(false);
    }
  }

  const preliminary = latest.data?.status === "preliminary";
  const pending = preliminary || latest.data?.rationale_note === "AI draft pending";
  // The recommendation is shown as soon as the rules engine finishes. A live road check (cache miss)
  // finalizes it later; the AI draft lands after that.
  useEffect(() => {
    const rec = latest.data;
    if (!pending || !rec) return;
    const started = Date.now();
    const t = setInterval(async () => {
      try {
        const fresh = await api.recommendation(rec.id);
        const settled = fresh.status !== "preliminary" && fresh.rationale_note !== "AI draft pending";
        if (settled || Date.now() - started > 240_000) {
          latest.setData(fresh);
          clearInterval(t);
        } else if (fresh.status !== latest.data?.status) {
          latest.setData(fresh); // preliminary -> final: show the final result while the AI draft runs
        }
      } catch {
        /* keep the template on screen; try again next tick */
      }
    }, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, latest.data?.id]);

  if (facilities.loading) return <Loading />;
  if (facilities.error) return <ErrorBox error={facilities.error} onRetry={facilities.reload} />;

  const rec = latest.data;
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
      <div className="min-w-0 space-y-4">
        <DemoScenario onChange={rec => { latest.setData(rec); setParams({facility: rec?.facility_id ?? "usda-kc-demo"}); }} />
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
                {facility.access_routes.map((r) => r.route).join(", ")}
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-sm">
              <span className="block text-xs font-semibold text-slate-600">Evaluate for</span>
              <select className="rounded border border-slate-300 bg-white px-2 py-2" value={target}
                      onChange={(e) => setTarget(e.target.value)}>
                <option value="next">Next operating day</option>
                {nextDays(8).map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
                {demo && <option value="demo">Today as a workday (DEMO)</option>}
              </select>
            </label>
            <Button onClick={reevaluate} disabled={evaluating || !facilityId}>
              {evaluating ? "Evaluating… (live NWS)" : "Re-evaluate"}
            </Button>
          </div>
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
              preliminary ? (
                <Button disabled title="Approval opens when the road check finishes">Approve or override →</Button>
              ) : (
                <Link to={`/decide?recommendation=${rec.id}`}>
                  <Button variant={rec.decision ? "secondary" : "primary"}>
                    {rec.decision ? "View decision & notifications" : "Approve or override →"}
                  </Button>
                </Link>
              )
            }
          />
        )}
      </div>
      <div className="space-y-4">
        {rec && <InvestigationPanel rec={rec} onRevision={async id => latest.setData(await api.recommendation(id))} />}
        <AgentActivity evaluationId={evaluating ? runningEval : rec?.evaluation_id ?? runningEval} live={evaluating || pending} />
      </div>
    </div>
  );
}

/** Today and the next days, labelled with the weekday. */
function nextDays(n: number): { value: string; label: string }[] {
  const out = [];
  for (let i = 0; i < n; i++) {
    const d = new Date();
    d.setDate(d.getDate() + i);
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const label = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    out.push({ value, label: i === 0 ? `Today (${label})` : label });
  }
  return out;
}
