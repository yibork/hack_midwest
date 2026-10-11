import { useMemo, useState } from "react";
import { api, type CoverageItem, type Decision, type Notification, type NoticeDetail } from "../api";
import DecisionClock from "./DecisionClock";
import { CoverageStrip, codeLabel } from "./People";
import { Button, Card, ErrorBox, Loading, fmtDateTime, fmtTime, humanText, useAsync } from "./ui";

type WaveFilter = "all" | "1" | "2" | "fyi" | "not_sent";
const PERSONAL = new Set(["action_required", "fyi", "call_in"]);

/** Step 3 of Decide: the notice (who gets what, when, how), staffing call-ins, and sending. */
export default function NoticePanel({ decision, tz, onSending }: { decision: Decision; tz: string; onSending: (s: boolean) => void }) {
  const detail = useAsync(() => api.decisionNotice(decision.id), [decision.id]);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  async function send() {
    setSending(true);
    onSending(true);
    setSendError(null);
    try {
      await api.notify(decision.id);
      await detail.reload();
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
      onSending(false);
    }
  }

  if (detail.loading && !detail.data) return <Card title="3 · Notice"><Loading /></Card>;
  if (detail.error) return <ErrorBox error={detail.error} onRetry={detail.reload} />;
  const d = detail.data!;
  const toSend = d.notifications.filter((n) => n.included && !n.sent_at && n.message);
  const sent = d.notice.sent_at != null;

  return (
    <>
      <Card
        title={`3 · Notice v${d.notice.version} (${d.notice.kind.replace("_", " ")})`}
        actions={
          <Button onClick={send} disabled={sending || toSend.length === 0}
                  variant={decision.final_posture === "closure" ? "danger" : "primary"}>
            {sending ? "Sending…" : toSend.length ? `Send ${toSend.length} messages (wave 1 first)` : "All sent"}
          </Button>
        }
      >
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div className="space-y-2 text-sm">
            {d.previous && (
              <p className="rounded bg-sky-50 px-3 py-2 text-sky-900">
                Supersedes v{d.previous.version} ({d.previous.posture.replace("_", " ")}, sent {fmtTime(d.previous.sent_at, tz)}).
                Changed instructions get an update; people no longer affected get an all clear; identical messages are not resent.
              </p>
            )}
            {Object.entries(d.notice.params).filter(([k]) => k !== "posture").map(([k, v]) => (
              <div key={k}>{k.replace(/_/g, " ")}: <strong>{v}</strong></div>
            ))}
            {sent && <div>Sent {fmtDateTime(d.notice.sent_at, tz)}</div>}
            <Summary rows={d.notifications} />
          </div>
          <DecisionClock decideBy={d.clock.at} timezone={tz} label={d.clock.label} />
        </div>
        {sendError && <div className="mt-3"><ErrorBox error={sendError} /></div>}
      </Card>

      <Staffing detail={d} onChanged={detail.reload} />
      <Recipients detail={d} tz={tz} />
    </>
  );
}

function Summary({ rows }: { rows: Notification[] }) {
  const inc = rows.filter((n) => n.included);
  const delivered = rows.filter((n) => n.sent_at && Object.values(n.channel_results).some((r) => r.ok)).length;
  const acks = rows.filter((n) => n.requires_ack);
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      <span><strong>{inc.length}</strong> to send</span>
      <span><strong>{rows.filter((n) => n.change === "unchanged").length}</strong> unchanged (not resent)</span>
      <span><strong>{delivered}</strong> delivered</span>
      <span><strong>{acks.filter((n) => n.acknowledged_at).length}/{acks.length}</strong> acknowledged</span>
      <span><strong>{rows.filter((n) => n.drafted_by === "agent").length}</strong> worded by the message agent</span>
    </div>
  );
}

function Staffing({ detail, onChanged }: { detail: NoticeDetail; onChanged: () => void }) {
  const short = detail.coverage.filter((c) => c.status !== "covered");
  return (
    <Card title="Critical function staffing">
      <CoverageStrip coverage={detail.coverage} />
      {short.length === 0 ? (
        <p className="mt-2 text-sm text-slate-600">All critical functions are covered for this posture.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {short.map((c) => <CallInPicker key={c.code} noticeId={detail.notice.id} item={c} onChanged={onChanged} />)}
          <p className="text-xs text-slate-500">
            Backups are ranked by commute (qualified, not on leave or detail, commute not exposed). Nobody is assigned
            automatically: a call-in asks the person to report and acknowledge.
          </p>
        </div>
      )}
    </Card>
  );
}

function CallInPicker({ noticeId, item, onChanged }: { noticeId: string; item: CoverageItem; onChanged: () => void }) {
  const [manager, setManager] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function callIn(employeeId: string) {
    setBusy(employeeId);
    setError(null);
    try {
      await api.callIn(noticeId, { employee_id: employeeId, function_code: item.code, manager_name: manager });
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="rounded border border-red-200 bg-red-50/40 p-3">
      <div className="font-semibold">{item.label}{item.gap ? ` · need ${item.gap} more` : ""}</div>
      {item.exposed.length > 0 && <div className="text-sm text-amber-800">Not counted (commute exposed): {item.exposed.join(", ")}</div>}
      {item.called_in.map((c) => (
        <div key={c.notification_id} className="text-sm">
          Called in: {c.name} · {c.acknowledged ? <span className="font-semibold text-green-700">acknowledged</span> : "awaiting ack"}
        </div>
      ))}
      {item.backups.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="text-sm">
            <span className="block text-xs text-slate-600">Your name (kept in the record)</span>
            <input className="rounded border border-slate-300 px-2 py-1" value={manager} onChange={(e) => setManager(e.target.value)} />
          </label>
          {item.backups.map((b) => (
            <Button key={b.employee_id} variant="secondary" disabled={!manager.trim() || busy !== null}
                    onClick={() => callIn(b.employee_id)}>
              {busy === b.employee_id ? "Calling in…" : `Call in ${b.name} (${b.commute_minutes} min, ${b.scheduled.replace("_", " ")})`}
            </Button>
          ))}
        </div>
      ) : item.status === "shortfall" && <div className="mt-1 text-sm text-red-800">No eligible backups.</div>}
      {error && <div className="mt-2"><ErrorBox error={error} /></div>}
    </div>
  );
}

function Recipients({ detail, tz }: { detail: NoticeDetail; tz: string }) {
  const [wave, setWave] = useState<WaveFilter>("all");
  const [query, setQuery] = useState("");
  const name = (id: string) => detail.names[id] ?? id;
  const rows = detail.notifications.filter((n) => PERSONAL.has(n.message_type) || n.message_type === "none" || n.included);
  const shown = rows.filter((n) =>
    (wave === "all" || (wave === "not_sent" ? !n.included : n.included && n.wave === wave)) &&
    (!query || name(n.employee_id).toLowerCase().includes(query.toLowerCase())));
  const groups = useMemo(() => {
    const m = new Map<string, Notification[]>();
    for (const n of shown) for (const c of n.reason_codes.length ? n.reason_codes : ["OTHER"]) {
      m.set(c, [...(m.get(c) ?? []), n]);
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [shown]);
  const counts: Record<WaveFilter, number> = {
    all: rows.length,
    "1": rows.filter((n) => n.included && n.wave === "1").length,
    "2": rows.filter((n) => n.included && n.wave === "2").length,
    fyi: rows.filter((n) => n.included && n.wave === "fyi").length,
    not_sent: rows.filter((n) => !n.included).length,
  };

  return (
    <Card title="Recipients by reason">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {(Object.keys(counts) as WaveFilter[]).map((f) => (
          <button key={f} onClick={() => setWave(f)}
                  className={`rounded-full px-3 py-1 text-sm ${wave === f ? "bg-slate-800 text-white" : "bg-slate-100 hover:bg-slate-200"}`}>
            {f === "all" ? "All" : f === "not_sent" ? "Not sent" : f === "fyi" ? "FYI" : `Wave ${f}`} ({counts[f]})
          </button>
        ))}
        <input className="ml-auto rounded border border-slate-300 px-2 py-1 text-sm" placeholder="Filter by name"
               value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Filter by name" />
      </div>
      <div className="space-y-2">
        {groups.map(([code, list]) => (
          <details key={code} className="rounded border border-slate-200" open={groups.length <= 3}>
            <summary className="cursor-pointer px-3 py-2 font-semibold">
              {codeLabel(code)}
              <span className="ml-2 rounded-full bg-slate-100 px-2 text-sm">{list.length}</span>
            </summary>
            <div className="overflow-x-auto px-3 pb-3">
              <table className="w-full text-sm">
                <thead className="text-left text-slate-500">
                  <tr>
                    <th className="py-1 pr-3">Person</th><th className="py-1 pr-3">Wave</th><th className="py-1 pr-3">Notify by</th>
                    <th className="py-1 pr-3">Channels</th><th className="py-1 pr-3">Instruction / access</th>
                    <th className="py-1 pr-3">Ack</th><th className="py-1">Message</th>
                  </tr>
                </thead>
                <tbody>{list.map((n) => <Row key={n.id + code} n={n} name={name(n.employee_id)} tz={tz} />)}</tbody>
              </table>
            </div>
          </details>
        ))}
        {groups.length === 0 && <p className="text-sm text-slate-500">Nobody matches.</p>}
      </div>
    </Card>
  );
}

function Row({ n, name, tz }: { n: Notification; name: string; tz: string }) {
  return (
    <tr className={`border-t border-slate-100 align-top ${n.included ? "" : "text-slate-500"}`}>
      <td className="py-2 pr-3">
        <div className="font-semibold">{name}</div>
        <div className="text-xs text-slate-500">{n.reason_text}</div>
        {n.change !== "initial" && <span className="rounded bg-sky-100 px-1 text-xs text-sky-900">{n.change.replace("_", " ")}</span>}
        {n.message_type !== "action_required" && <span className="ml-1 rounded bg-slate-100 px-1 text-xs">{n.message_type.replace("_", " ")}</span>}
      </td>
      <td className="py-2 pr-3">{n.included ? n.wave : "—"}</td>
      <td className="py-2 pr-3 whitespace-nowrap">{fmtTime(n.notify_by, tz)}</td>
      <td className="py-2 pr-3 whitespace-nowrap">
        {n.included ? n.channels_planned.join(" + ") : "—"}
        {Object.entries(n.channel_results).map(([ch, r]) => (
          <div key={ch} className={`text-xs ${r.ok ? "text-green-700" : "text-amber-700"}`}>
            {ch}{r.fallback ? " (fallback)" : ""}: {r.ok ? "✓" : r.error ?? "failed"}
          </div>
        ))}
      </td>
      <td className="py-2 pr-3">
        <div>{n.duty_instruction}</div>
        <div className="text-xs text-slate-500">{humanText(n.building_access)}</div>
      </td>
      <td className="py-2 pr-3 whitespace-nowrap text-xs">
        {!n.requires_ack ? "—" : n.acknowledged_at ? <span className="font-semibold text-green-700">✓ {fmtTime(n.acknowledged_at, tz)}</span>
          : n.escalation_level ? <span className="font-semibold text-red-700">escalated (level {n.escalation_level})</span>
          : n.sent_at ? "waiting" : "required"}
      </td>
      <td className="max-w-sm py-2">
        {n.message ? (
          <details>
            <summary className="cursor-pointer text-xs text-slate-600">
              {n.drafted_by === "agent" ? "message agent" : "template"} · {n.subject}
            </summary>
            <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2 font-sans text-xs">{n.message}</pre>
          </details>
        ) : "—"}
      </td>
    </tr>
  );
}
