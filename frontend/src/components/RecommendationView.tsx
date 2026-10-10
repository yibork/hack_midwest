import { useState, type ReactNode } from "react";
import type { Evidence, RecommendationDetail } from "../api";
import DecisionClock from "./DecisionClock";
import { PeopleStrip } from "./People";
import { Button, Card, ConfidenceBadge, PostureBadge, StatusBadge, fmtDateTime } from "./ui";

/** The full recommendation: posture, clock, triggers, confidence, what would change, rationale, evidence. */
export default function RecommendationView({ rec, actions }: { rec: RecommendationDetail; actions?: ReactNode }) {
  const tz = rec.facility.timezone;
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-sm uppercase tracking-wide text-slate-500">
              Recommended posture for {rec.operating_date}
            </div>
            <div className="mt-2">
              <PostureBadge posture={rec.posture} size="xl" />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-slate-600">
              <ConfidenceBadge confidence={rec.confidence} />
              <span>Evaluated {fmtDateTime(rec.evaluated_at, tz)}</span>
              <span className="font-mono text-xs">policy {rec.policy_version}</span>
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
                    <td className="py-2 pr-3 tabular-nums">{t.margin != null ? `${t.margin > 0 ? "+" : ""}${t.margin} ${t.units ?? ""}` : "—"}</td>
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
            {rec.rationale_source === "agent" ? (
              <span className="rounded bg-indigo-100 px-2 py-0.5 text-xs font-semibold normal-case tracking-normal text-indigo-800">
                Writer agent · validated
              </span>
            ) : (
              <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold normal-case tracking-normal text-amber-900">
                LLM offline — template rationale
              </span>
            )}
          </span>
        }
        actions={<CopyButton text={rec.rationale} />}
      >
        <p className="leading-relaxed">{rec.rationale}</p>
      </Card>

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
                <td className="py-2 pr-3 font-mono uppercase">{e.source}</td>
                <td className="py-2 pr-3">
                  <div className="font-semibold">{e.kind.replace(/_/g, " ")}</div>
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
