import { useState } from "react";
import { api } from "../api";
import { Button, ErrorBox, Loading, fmtTime, humanText, useAsync } from "../components/ui";

/** Employee: what to do today, building access, and the acknowledge button. */
export default function MyStatusPage() {
  const me = useAsync(() => api.me(), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (me.loading && !me.data) return <Loading />;
  if (me.error) return <ErrorBox error={me.error} onRetry={me.reload} />;
  const s = me.data!;
  const n = s.notification;
  const tz = s.facility_timezone || "America/Chicago";
  const spanish = s.employee.language === "es";
  const lines = s.message_lines || [s.facility_name, `**${humanText(s.duty_instruction)}**`];

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
    <div className="personal-status text-lg">
      <div className="rounded border border-slate-200 bg-white p-5">
        <h1 className="mb-3 text-2xl font-bold">{spanish ? "Hola" : "Hello"}, {s.employee.name.split(" ")[0]}</h1>
        {lines.map((line, index) => {
          if (/^(Please confirm:|Confirme:)/.test(line) && n?.requires_ack) return <div key={index} className="mt-5">
            {n.acknowledged_at ? <p className="font-semibold text-green-700">{spanish ? "Confirmado" : "Acknowledged"} {fmtTime(n.acknowledged_at, tz)}.</p> :
              <Button onClick={ack} disabled={busy} className="!px-6 !py-3 !text-lg">{busy ? (spanish ? "Guardando…" : "Recording…") : (spanish ? "Confirmar recepción" : "I got this: acknowledge")}</Button>}
          </div>;
          return <p key={index} className={index === 1 ? "instruction mt-4" : "mt-3 text-base"}>
            {line.startsWith("**") ? <strong>{line.replace(/^\*\*|\*\*$/g, "")}</strong> : line}
          </p>;
        })}
        {error && <div className="mt-3"><ErrorBox error={error} /></div>}
      </div>
    </div>
  );
}
