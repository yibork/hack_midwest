import { useEffect, useState } from "react";
import { fmtTime } from "./ui";

/** "Decide by HH:MM" with a live countdown. decideBy = earliest included departure - 30 min. */
export default function DecisionClock({ decideBy, timezone }: { decideBy: string | null; timezone: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!decideBy) {
    return <div className="text-slate-500">No decision deadline (no one to notify).</div>;
  }
  const ms = new Date(decideBy).getTime() - now;
  const overdue = ms < 0;
  const abs = Math.abs(ms);
  const h = Math.floor(abs / 3_600_000);
  const m = Math.floor((abs % 3_600_000) / 60_000);
  const s = Math.floor((abs % 60_000) / 1000);
  const countdown = `${h > 0 ? `${h}h ` : ""}${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;

  return (
    <div>
      <div className="text-sm uppercase tracking-wide text-slate-500">Decide by</div>
      <div className="text-3xl font-bold tabular-nums">{fmtTime(decideBy, timezone, true)}</div>
      <div className={`mt-1 text-lg font-semibold tabular-nums ${overdue ? "text-red-700" : ms < 3_600_000 ? "text-orange-600" : "text-slate-700"}`}>
        {overdue ? `Overdue by ${countdown}` : `${countdown} left`}
      </div>
      <div className="text-xs text-slate-500 mt-1">Earliest departure of included staff minus 30 min</div>
    </div>
  );
}
