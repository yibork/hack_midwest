import { useState, type ReactNode } from "react";
import type { Evidence, RecommendationDetail } from "../api";
import DecisionClock from "./DecisionClock";
import MapView from "./MapView";
import { PeopleStrip } from "./People";
import LocalSignals, { AuthorityBadge, LocalEvidenceDetail, isLocal } from "./LocalSignals";
import { Button, Card, ConfidenceBadge, PostureBadge, StatusBadge, fmtDateTime } from "./ui";

/** The full recommendation: posture, clock, triggers, confidence, what would change, rationale, evidence. */
export default function RecommendationView({ rec, actions }: { rec: RecommendationDetail; actions?: ReactNode }) {
  const tz = rec.facility.timezone;
  return (
    <div className="space-y-4">
      {rec.status === "preliminary" && (
        <div role="status" className="flex items-center gap-2 rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 font-semibold text-sky-900">
          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-sky-300 border-t-sky-700" />
          Preliminary - road check in progress. Approval opens when it finishes; the result may still change.
        </div>
      )}
      <Card>
        <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-sm uppercase tracking-wide text-slate-500">
              Recommended posture for {new Date(`${rec.operating_date}T12:00:00`).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}
            </div>
            {rec.target_note && (
              <div className={`mt-1 text-sm ${rec.target_mode === "demo_workday" ? "rounded bg-yellow-200 px-2 py-0.5 font-semibold text-slate-900" : "text-slate-600"}`}>
                {rec.target_note}
              </div>
            )}
            <div className="mt-2">
              <PostureBadge posture={rec.posture} size="xl" />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-slate-600">
              <ConfidenceBadge confidence={rec.confidence} />
              <span>Evaluated {fmtDateTime(rec.evaluated_at, tz)}</span>
              <span className="font-mono text-xs">policy {rec.policy_version}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-4 text-sm text-slate-600">
              {rec.lead_time_hours != null && <span>Forecast lead time: {Math.round(rec.lead_time_hours)} h to the commute window</span>}
              <span>Next scheduled re-evaluation: {rec.next_reevaluation ? fmtDateTime(rec.next_reevaluation, tz) : "none scheduled"}</span>
            </div>
            {rec.decision && (
              <div className="mt-3 rounded bg-slate-100 px-3 py-2 text-sm">
                <span className="font-semibold">{rec.decision.action === "approve" ? "Approved" : "Overridden"}</span> by{" "}
                {rec.decision.manager_name} at {fmtDateTime(rec.decision.decided_at, tz)} → final posture{" "}
                <PostureBadge posture={rec.decision.final_posture} size="sm" />
                {rec.decision.reason && <div className="mt-1 italic">“{rec.decision.reason}”</div>}
              </div>
            )}
          </div>
          <div className="flex flex-col items-start gap-3 md:items-end">
            <DecisionClock decideBy={rec.decide_by} timezone={tz} label={rec.people?.clock.label} />
            {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
          </div>
        </div>
        {rec.people && (
          <div className="mt-4 border-t border-slate-200 pt-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              People and staffing if this posture is approved
            </div>
            <PeopleStrip people={rec.people} />
          </div>
        )}
      </Card>

      <Card title={`Triggers (${rec.triggers.length})`}>
        {rec.triggers.length === 0 ? (
          <p className="text-slate-600">No policy thresholds were met.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500">
                <tr>
                  <th className="py-1 pr-3">Rule</th>
                  <th className="py-1 pr-3">Observed</th>
                  <th className="py-1 pr-3">Threshold</th>
                  <th className="py-1 pr-3">Margin</th>
                  <th className="py-1 pr-3">→ Posture</th>
                  <th className="py-1">Citation</th>
                </tr>
              </thead>
              <tbody>
                {rec.triggers.map((t) => (
                  <tr key={t.rule_id} className="border-t border-slate-100 align-top">
                    <td className="py-2 pr-3">
                      <div className="font-mono text-xs">{t.rule_id}</div>
                      <div className="text-slate-600">{t.description}</div>
                    </td>
                    <td className="py-2 pr-3 font-semibold">{t.observed}{t.units ? ` ${t.units}` : ""}</td>
                    <td className="py-2 pr-3">{t.threshold}{t.units ? ` ${t.units}` : ""}</td>
                    <td className="py-2 pr-3 tabular-nums">{t.margin == null ? "—" : typeof t.margin === "string" ? t.margin : `${t.margin > 0 ? "+" : ""}${t.margin} ${t.units ?? ""}`}</td>
                    <td className="py-2 pr-3"><PostureBadge posture={t.posture} size="sm" /></td>
                    <td className={`py-2 text-xs ${t.citation.startsWith("TODO") ? "text-amber-700" : "text-slate-600"}`}>{t.citation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Confidence">
          <ConfidenceBadge confidence={rec.confidence} />
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
            {rec.confidence_reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </Card>
        <Card title="What would change this">
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {rec.what_would_change.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </Card>
      </div>

      <Card
        title={
          <span className="flex flex-wrap items-center gap-2">
            Rationale
            <span className={`rounded px-2 py-0.5 text-xs font-semibold normal-case tracking-normal ${
              rec.rationale_note === "AI draft pending" || rec.status === "preliminary" ? "bg-sky-100 text-sky-900"
                : rec.rationale_source === "agent" ? "bg-indigo-100 text-indigo-800" : "bg-amber-100 text-amber-900"}`}>
              {rec.rationale_note === "AI draft pending" && (
                <span className="mr-1 inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-sky-300 border-t-sky-700 align-middle" />
              )}
              {rec.rationale_note ?? (rec.rationale_source === "agent" ? "AI draft" : "Template")}
            </span>
          </span>
        }
        actions={<CopyButton text={rec.rationale} />}
      >
        <p className="leading-relaxed">{rec.rationale}</p>
      </Card>

      <LocalSignals evidence={rec.evidence} tz={tz} />
      {!rec.facility.temporary && <MapView recId={rec.id} title="Map: facility, access routes, commute bands, local facts" />}

      <EvidenceList evidence={rec.evidence} tz={tz} />
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="secondary"
      className="!py-1"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

export function EvidenceList({ evidence, tz }: { evidence: Evidence[]; tz: string }) {
  const bad = evidence.filter((e) => e.status !== "ok").length;
  return (
    <Card title={`Evidence (${evidence.length})${bad ? ` · ${bad} not OK` : ""}`}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1 pr-3">Status</th>
              <th className="py-1 pr-3">Source</th>
              <th className="py-1 pr-3">Kind</th>
              <th className="py-1 pr-3">Source time</th>
              <th className="py-1 pr-3">Retrieved</th>
              <th className="py-1">Snapshot</th>
            </tr>
          </thead>
          <tbody>
            {evidence.map((e) => (
              <tr key={e.id} className={`border-t border-slate-100 align-top ${e.status === "unknown" ? "bg-red-50" : e.status === "stale" ? "bg-amber-50" : ""}`}>
                <td className="py-2 pr-3"><StatusBadge status={e.status} /></td>
                <td className="py-2 pr-3 font-mono uppercase">
                  {e.source}
                  {isLocal(e) && <div className="mt-1"><AuthorityBadge authority={(e.value as { authority: string }).authority} /></div>}
                </td>
                <td className="py-2 pr-3">
                  <div className="font-semibold">{e.kind.replace(/_/g, " ")}</div>
                  <LocalEvidenceDetail e={e} />
                  <div className="text-xs text-slate-500">{e.relevance_note}</div>
                  {e.ambiguity_flags?.map((f) => (
                    <div key={f} className="text-xs font-semibold text-amber-800">⚠ {f}</div>
                  ))}
                  {e.status === "unknown" && (
                    <div className="text-xs font-semibold text-red-700">
                      {(e.value as { error?: string } | null)?.error ?? "No data"} — not treated as safe
                    </div>
                  )}
                </td>
                <td className="py-2 pr-3 whitespace-nowrap">{fmtDateTime(e.source_time, tz)}</td>
                <td className="py-2 pr-3 whitespace-nowrap">{fmtDateTime(e.retrieved_at, tz)}</td>
                <td className="py-2 font-mono text-xs">
                  {e.url ? <a className="underline" href={e.url} target="_blank" rel="noreferrer">{e.snapshot_hash?.slice(0, 10) ?? "source"}</a> : e.snapshot_hash?.slice(0, 10) ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
