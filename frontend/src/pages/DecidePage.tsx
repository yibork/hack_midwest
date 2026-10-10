import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { POSTURES, api, type Decision, type Notification, type Posture, type Recipient, type RecommendationDetail } from "../api";
import AgentActivity from "../components/AgentActivity";
import { Button, Card, ConfidenceBadge, ErrorBox, Loading, PostureBadge, fmtDateTime, postureLabel, useAsync } from "../components/ui";

type Filter = "all" | "included" | "excluded" | "essential";

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

function DecideFlow({ rec }: { rec: RecommendationDetail }) {
  const [decision, setDecision] = useState<Decision | null>(rec.decision);
  const [sending, setSending] = useState(false);
  const tz = rec.facility.timezone;

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
      <div className="min-w-0 space-y-4">
        <h1 className="text-2xl font-bold">Decide: {rec.facility.name}</h1>

        <Card title="1 · Recommendation">
          <div className="flex flex-wrap items-center gap-4">
            <PostureBadge posture={rec.posture} size="md" />
            <ConfidenceBadge confidence={rec.confidence} />
            <span className="text-sm text-slate-600">
              for {rec.operating_date} · evaluated {fmtDateTime(rec.evaluated_at, tz)} · {rec.triggers.length} trigger(s)
            </span>
            <Link className="text-sm underline" to={rec.facility.temporary ? "/national" : `/facility?facility=${rec.facility_id}`}>
              Full details
            </Link>
          </div>
          <p className="mt-3 text-sm text-slate-700">{rec.rationale}</p>
        </Card>

        {decision ? (
          <Card title="2 · Decision recorded">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-semibold">{decision.action === "approve" ? "Approved" : "Overridden"}</span>
              by {decision.manager_name} at {fmtDateTime(decision.decided_at, tz)} →
              <PostureBadge posture={decision.final_posture} />
            </div>
            {decision.reason && <p className="mt-2 italic">“{decision.reason}”</p>}
          </Card>
        ) : (
          <DecisionForm rec={rec} onDecided={setDecision} />
        )}

        {decision && <Recipients decision={decision} onSending={setSending} />}
      </div>
      <AgentActivity evaluationId={rec.evaluation_id} live={sending} />
    </div>
  );
}

function DecisionForm({ rec, onDecided }: { rec: RecommendationDetail; onDecided: (d: Decision) => void }) {
  const [manager, setManager] = useState("");
  const [action, setAction] = useState<"approve" | "override">("approve");
  const [finalPosture, setFinalPosture] = useState<Posture>(rec.posture);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const overrideInvalid = action === "override" && (!reason.trim() || finalPosture === rec.posture);
  const canSubmit = manager.trim() && !overrideInvalid && !busy;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onDecided(await api.decide(rec.id, action === "approve"
        ? { manager_name: manager, action }
        : { manager_name: manager, action, final_posture: finalPosture, reason }));
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
        {error && <ErrorBox error={error} />}
        <Button type="submit" disabled={!canSubmit}>{busy ? "Recording…" : "Record decision"}</Button>
      </form>
    </Card>
  );
}

function Recipients({ decision, onSending }: { decision: Decision; onSending: (s: boolean) => void }) {
  const recipients = useAsync(() => api.recipients(decision.id), [decision.id]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  useEffect(() => {
    api.notifications(decision.id).then(setNotifications).catch(() => {});
  }, [decision.id]);

  const byEmployee = useMemo(() => new Map(notifications.map((n) => [n.employee_id, n])), [notifications]);
  const list = recipients.data ?? [];
  const counts: Record<Filter, number> = {
    all: list.length,
    included: list.filter((r) => r.included).length,
    excluded: list.filter((r) => !r.included).length,
    essential: list.filter((r) => r.essential).length,
  };
  const shown = list.filter((r) => filter === "all" || (filter === "included" ? r.included : filter === "excluded" ? !r.included : r.essential));
  const sent = notifications.length > 0;

  async function send() {
    setSending(true);
    onSending(true);
    setSendError(null);
    try {
      setNotifications(await api.notify(decision.id));
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
      onSending(false);
    }
  }

  return (
    <Card
      title="3 · Recipients"
      actions={
        <Button onClick={send} disabled={sending || sent || counts.included === 0} variant={decision.final_posture === "closure" ? "danger" : "primary"}>
          {sent ? "Notifications sent" : sending ? "Sending…" : `Send notifications (${counts.included})`}
        </Button>
      }
    >
      {recipients.loading && <Loading />}
      {recipients.error && <ErrorBox error={recipients.error} onRetry={recipients.reload} />}
      {sendError && <ErrorBox error={sendError} />}
      {sent && (
        <p className="mb-3 rounded bg-slate-100 px-3 py-2 text-sm">
          Sent {fmtDateTime(notifications.find((n) => n.sent_at)?.sent_at)} ·{" "}
          {notifications.filter((n) => n.delivered).length} delivered,{" "}
          {notifications.filter((n) => n.included && !n.delivered).length} not delivered (channel not configured or failed; see server log)
        </p>
      )}
      <div className="mb-3 flex flex-wrap gap-1">
        {(Object.keys(counts) as Filter[]).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
                  className={`rounded-full px-3 py-1 text-sm ${filter === f ? "bg-slate-800 text-white" : "bg-slate-100 hover:bg-slate-200"}`}>
            {f} ({counts[f]})
          </button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1 pr-3">Employee</th>
              <th className="py-1 pr-3">Status</th>
              <th className="py-1 pr-3">Reason</th>
              <th className="py-1 pr-3">Channel</th>
              <th className="py-1 pr-3">Message</th>
              {sent && <th className="py-1">Delivery</th>}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => <RecipientRow key={r.employee_id} r={r} n={byEmployee.get(r.employee_id)} sent={sent} />)}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** Before sending: the template draft. After: the message actually sent (agent-written or template). */
function MessagePreview({ message, sent }: { message: string | null; sent: boolean }) {
  if (!message) return <>—</>;
  const lines = message.split("\n");
  const preview = (lines.length > 1 ? lines[1] : lines[0]).slice(0, 60);
  return (
    <details>
      <summary className="cursor-pointer text-slate-700">
        {sent && <span className="mr-1 rounded bg-slate-100 px-1 text-xs">sent</span>}
        {preview}…
      </summary>
      <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2 font-sans text-xs">{message}</pre>
    </details>
  );
}

function RecipientRow({ r, n, sent }: { r: Recipient; n?: Notification; sent: boolean }) {
  return (
    <tr className={`border-t border-slate-100 align-top ${r.included ? "" : "text-slate-500"}`}>
      <td className="py-2 pr-3">
        <div className="font-semibold">{r.name}</div>
        <div className="flex flex-wrap gap-1 text-xs">
          {r.essential && <span className="rounded bg-purple-100 px-1 text-purple-800">essential</span>}
          {r.early_notice && <span className="rounded bg-sky-100 px-1 text-sky-800">early notice</span>}
          {r.language === "es" && <span className="rounded bg-slate-100 px-1">ES</span>}
          <span className="text-slate-500">departs {r.typical_departure_time}</span>
        </div>
      </td>
      <td className="py-2 pr-3">
        <span className={`rounded px-2 py-0.5 text-xs font-bold ${r.included ? "bg-green-100 text-green-800" : "bg-slate-200 text-slate-600"}`}>
          {r.included ? "INCLUDED" : "EXCLUDED"}
        </span>
      </td>
      <td className="py-2 pr-3">{r.reason}</td>
      <td className="py-2 pr-3 whitespace-nowrap">{r.channel}<div className="text-xs text-slate-500">{r.contact}</div></td>
      <td className="max-w-md py-2 pr-3">
        <MessagePreview message={n?.message ?? r.message} sent={!!n?.message} />
      </td>
      {sent && (
        <td className="py-2 whitespace-nowrap">
          {!r.included ? "—" : n?.delivered ? <span className="font-semibold text-green-700">✓ delivered</span>
            : <span className="font-semibold text-amber-700">✗ not delivered</span>}
        </td>
      )}
    </tr>
  );
}
