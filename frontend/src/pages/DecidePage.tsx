import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { POSTURES, api, type Decision, type Posture, type RecommendationDetail } from "../api";
import AgentActivity from "../components/AgentActivity";
import InvestigationPanel from "../components/InvestigationPanel";
import NoticePanel from "../components/NoticePanel";
import RecommendationHero from "../components/RecommendationHero";
import { PeopleStrip } from "../components/People";
import { Button, Card, ErrorBox, Loading, PostureBadge, fmtDateTime, postureLabel, humanText, useAsync } from "../components/ui";

export default function DecidePage() {
  const [params] = useSearchParams();
  const recId = params.get("recommendation");

  // Without ?recommendation=, use the first facility's latest recommendation.
  const rec = useAsync<RecommendationDetail | null>(async () => {
    if (recId) return api.recommendation(recId);
    const facilities = await api.facilities();
    return facilities[0] ? api.latestRecommendation(facilities[0].id) : null;
  }, [recId]);

  if (rec.loading) return <Loading />;
  if (rec.error) return <ErrorBox error={rec.error} onRetry={rec.reload} />;
  if (!rec.data) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-slate-600">
        Nothing to decide yet. <Link className="underline" to="/facility">Evaluate a facility first.</Link>
      </div>
    );
  }
  return <DecideFlow key={rec.data.id} rec={rec.data} />;
}

function DecideFlow({ rec: initial }: { rec: RecommendationDetail }) {
  const [rec, setRec] = useState(initial);
  const [decision, setDecision] = useState<Decision | null>(rec.decision);
  const [sending, setSending] = useState(false);
  const tz = rec.facility.timezone;
  useEffect(() => {
    if (rec.status !== 'preliminary') return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {const updated = await api.recommendation(rec.id); if (!cancelled) setRec(updated);}
      catch { /* The investigation panel shows progress errors and retries. */ }
    }, 1500);
    return () => {cancelled = true; clearInterval(timer);};
  }, [rec.id, rec.status]);

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
      <div className="min-w-0 space-y-4">
        <h1 className="text-2xl font-bold">Decide: {rec.facility.name}</h1>

        <RecommendationHero rec={rec} actions={<Link className="text-sm underline" to={rec.facility.temporary ? "/national" : `/facility?facility=${rec.facility_id}`}>Review full evidence</Link>} />
        <Card title="Evidence and staffing"><details><summary>Why this recommendation</summary><p className="mt-2 text-sm text-slate-700">{humanText(rec.rationale)}</p></details>
          {rec.people && <div className="mt-3"><PeopleStrip people={rec.people} /></div>}</Card>

        {decision ? (
          <Card title="2 · Decision recorded">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-semibold">{decision.action === "approve" ? "Approved" : "Overridden"}</span>
              by {decision.manager_name} at {fmtDateTime(decision.decided_at, tz)} →
              <PostureBadge posture={decision.final_posture} />
            </div>
            {decision.reason && <p className="mt-2 italic">“{decision.reason}”</p>}
            {(decision.delay_hours || decision.dismissal_time) && (
              <p className="mt-1 text-sm text-slate-600">
                {decision.delay_hours ? `Delay: ${decision.delay_hours} h after each shift start` : ""}
                {decision.dismissal_time ? `Dismissal: ${decision.dismissal_time}` : ""}
              </p>
            )}
          </Card>
        ) : rec.status === "preliminary" ? (
          <Card title="2 · Approve or override">
            <p className="text-slate-700">Preliminary — evidence check in progress. This page updates automatically; approval opens when it finishes.</p>
            <Button className="mt-3" variant="secondary" onClick={() => window.location.reload()}>Check again</Button>
          </Card>
        ) : (
          <DecisionForm rec={rec} onDecided={setDecision} />
        )}

        {decision && !rec.facility.temporary && <NoticePanel decision={decision} tz={tz} onSending={setSending} />}
      </div>
      <div className="space-y-4"><InvestigationPanel rec={rec} onRevision={async id => {
        setRec(await api.recommendation(id)); setDecision(null);
      }} /><AgentActivity evaluationId={rec.evaluation_id} live={sending} /></div>
    </div>
  );
}

function DecisionForm({ rec, onDecided }: { rec: RecommendationDetail; onDecided: (d: Decision) => void }) {
  const [manager, setManager] = useState("");
  const [action, setAction] = useState<"approve" | "override">("approve");
  const [finalPosture, setFinalPosture] = useState<Posture>(rec.posture);
  const [reason, setReason] = useState("");
  const [delayHours, setDelayHours] = useState(2);
  const [dismissal, setDismissal] = useState("14:00");
  const [busy, setBusy] = useState(false);
  const effective = action === "approve" ? rec.posture : finalPosture;
  const [error, setError] = useState<string | null>(null);

  const overrideInvalid = action === "override" && (!reason.trim() || finalPosture === rec.posture);
  const canSubmit = manager.trim() && !overrideInvalid && !busy;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const params = effective === "delayed_arrival" ? { delay_hours: delayHours }
        : effective === "early_dismissal" ? { dismissal_time: dismissal } : {};
      onDecided(await api.decide(rec.id, action === "approve"
        ? { manager_name: manager, action, ...params }
        : { manager_name: manager, action, final_posture: finalPosture, reason, ...params }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="2 · Approve or override">
      <form onSubmit={submit} className="space-y-4">
        <label className="block">
          <span className="text-sm font-semibold">Manager name</span>
          <input className="mt-1 block w-full max-w-sm rounded border border-slate-300 px-3 py-2" value={manager}
                 onChange={(e) => setManager(e.target.value)} placeholder="Your name" required />
        </label>
        <div className="flex flex-wrap gap-3">
          {(["approve", "override"] as const).map((a) => (
            <label key={a} className={`flex cursor-pointer items-center gap-2 rounded border px-4 py-3 ${action === a ? "border-slate-800 bg-slate-50" : "border-slate-300"}`}>
              <input type="radio" name="action" checked={action === a} onChange={() => setAction(a)} />
              {a === "approve" ? <>Approve <PostureBadge posture={rec.posture} size="sm" /></> : "Override"}
            </label>
          ))}
        </div>
        {action === "override" && (
          <div className="space-y-3 rounded border border-amber-300 bg-amber-50 p-3">
            <label className="block">
              <span className="text-sm font-semibold">Final posture</span>
              <select className="mt-1 block rounded border border-slate-300 bg-white px-3 py-2" value={finalPosture}
                      onChange={(e) => setFinalPosture(e.target.value as Posture)}>
                {POSTURES.map((p) => <option key={p} value={p}>{postureLabel(p)}</option>)}
              </select>
              {finalPosture === rec.posture && <span className="text-xs text-amber-800">Pick a posture different from the recommendation.</span>}
            </label>
            <label className="block">
              <span className="text-sm font-semibold">Reason (required, kept in the record)</span>
              <textarea className="mt-1 block w-full rounded border border-slate-300 px-3 py-2" rows={3} value={reason}
                        onChange={(e) => setReason(e.target.value)} required
                        placeholder="e.g. Road crews report I-35 icing not reflected in the forecast" />
            </label>
          </div>
        )}
        {effective === "delayed_arrival" && (
          <label className="block">
            <span className="text-sm font-semibold">Delay (hours after each person's shift start)</span>
            <select className="mt-1 block rounded border border-slate-300 bg-white px-3 py-2" value={delayHours}
                    onChange={(e) => setDelayHours(Number(e.target.value))}>
              {[1, 2, 3, 4].map((h) => <option key={h} value={h}>{h} hour{h > 1 ? "s" : ""}</option>)}
            </select>
          </label>
        )}
        {effective === "early_dismissal" && (
          <label className="block">
            <span className="text-sm font-semibold">Dismissal time ({rec.facility.timezone})</span>
            <input type="time" className="mt-1 block rounded border border-slate-300 px-3 py-2" value={dismissal}
                   onChange={(e) => setDismissal(e.target.value)} required />
          </label>
        )}
        {error && <ErrorBox error={error} />}
        <p className="text-xs text-slate-500">Recording the decision prepares every message. Nothing is sent until you press Send.</p>
        <Button type="submit" disabled={!canSubmit}>{busy ? "Recording…" : "Record decision"}</Button>
      </form>
    </Card>
  );
}
