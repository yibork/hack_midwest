import { useState } from "react";
import { api } from "../api";
import { Button, ErrorBox, Loading, PostureBadge, fmtTime, useAsync } from "../components/ui";

/** Employee: what to do today, building access, and the acknowledge button. */
export default function MyStatusPage() {
  const me = useAsync(() => api.me(), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (me.loading && !me.data) return <Loading />;
  if (me.error) return <ErrorBox error={me.error} onRetry={me.reload} />;
  const s = me.data!;
  const n = s.notification;
  const tz = "America/Chicago";

  async function ack() {
    if (!n) return;
    setBusy(true);
    setError(null);
    try {
      await api.ack(n.id);
      await me.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-4 text-lg">
      <h1 className="text-2xl font-bold">Hello, {s.employee.name.split(" ")[0]}</h1>
      <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="text-sm uppercase tracking-wide text-slate-500">{s.facility_name} · {s.operating_date}</div>
        {s.posture && <div className="mt-2"><PostureBadge posture={s.posture} /></div>}
        <div className="mt-4 text-sm font-semibold uppercase text-slate-500">What to do</div>
        <div className="text-2xl font-bold">{s.duty_instruction}</div>
        <div className="mt-4 text-sm font-semibold uppercase text-slate-500">Building access</div>
        <div className="font-mono">{s.building_access}</div>
        {n?.audience_text && <p className="mt-4 text-sm text-slate-600">Why: {n.audience_text}</p>}
        {n?.requires_ack && (
          <div className="mt-5">
            {n.acknowledged_at ? (
              <p className="font-semibold text-green-700">Acknowledged at {fmtTime(n.acknowledged_at, tz)}. Thank you.</p>
            ) : (
              <Button onClick={ack} disabled={busy} className="!px-6 !py-3 !text-lg">{busy ? "Recording…" : "I got this: acknowledge"}</Button>
            )}
          </div>
        )}
        {error && <div className="mt-3"><ErrorBox error={error} /></div>}
      </div>
      {n?.message && (
        <details className="rounded border border-slate-200 bg-white p-3 text-base">
          <summary className="cursor-pointer">The message you were sent</summary>
          <pre className="mt-2 whitespace-pre-wrap font-sans">{n.message}</pre>
        </details>
      )}
    </div>
  );
}
