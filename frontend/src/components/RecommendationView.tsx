import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Evidence, RecommendationDetail } from "../api";
import RecommendationHero from "./RecommendationHero";
import AirQuality from './AirQuality';
import SavedCopyLinks from "./SavedCopyLinks";
import Disclosure from './Disclosure';
import {CitedAnswer} from './InvestigationPanel';
import MapView from "./MapView";
import { PeopleStrip } from "./People";
import LocalSignals, { AuthorityBadge, LocalEvidenceDetail, isLocal } from "./LocalSignals";
import { Button, Card, ConfidenceBadge, PostureBadge, StatusBadge, fmtDateTime, humanText, ruleLabel } from "./ui";

/** The full recommendation: posture, clock, triggers, confidence, what would change, rationale, evidence. */
export default function RecommendationView({ rec, actions }: { rec: RecommendationDetail; actions?: ReactNode }) {
  const tz = rec.facility.timezone;
  return (
    <div className="space-y-4">
      {rec.status === "preliminary" && (
        <div role="status" className="flex items-center gap-2 rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 font-semibold text-sky-900">
          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-sky-300 border-t-sky-700" />
          Preliminary - evidence check in progress. Approval opens when it finishes; the result may still change.
        </div>
      )}
      <RecommendationHero rec={rec} actions={actions} />
      {rec.people && <Card title="Who is affected"><PeopleStrip people={rec.people} /></Card>}
      <Card title="What would change this"><NearestThresholds rec={rec} /></Card>

      <Disclosure title="Triggers" summary={rec.triggers.length ? `${rec.triggers.length} policy thresholds met` : 'No policy thresholds met'}>
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
                      <Link className="font-semibold underline" to={`/thresholds#${t.rule_id}`}>{ruleLabel(t.rule_id)}</Link>
                      <div className="text-slate-600">{humanText(t.description)}</div>
                    </td>
                    <td className="py-2 pr-3 font-semibold">{t.observed}{t.units ? ` ${t.units}` : ""}</td>
                    <td className="py-2 pr-3">{t.threshold}{t.units ? ` ${t.units}` : ""}</td>
                    <td className="py-2 pr-3 tabular-nums">{t.margin == null ? "—" : typeof t.margin === "string" ? t.margin : `${t.margin > 0 ? "+" : ""}${t.margin} ${t.units ?? ""}`}</td>
                    <td className="py-2 pr-3"><PostureBadge posture={t.posture} size="sm" /></td>
                    <td className="py-2 text-xs text-slate-600"><Link className="underline" to={`/thresholds#${t.rule_id}`}>{typeof t.citation === 'string' ? t.citation : t.citation.title} · View threshold and verification</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      </Disclosure>

      <Disclosure title="Confidence details" summary={`${rec.confidence} — ${humanText(rec.confidence_reasons[0] ?? 'See confidence reasons')}`}>
        <Card title="Confidence">
          <ConfidenceBadge confidence={rec.confidence} />
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
            {rec.confidence_reasons.map((r, i) => <li key={i}>{humanText(r)}</li>)}
          </ul>
        </Card>
      </Disclosure>

      <Disclosure title="Rationale" summary={rec.rationale_note ?? 'Saved explanation and citations'}>
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
        actions={<CopyButton text={humanText(rec.rationale)} />}
      >
        <p className="leading-relaxed"><CitedAnswer text={humanText(rec.rationale)} /></p>
        <details className="mt-2 text-sm"><summary>Technical details</summary><pre>{rec.rationale}</pre></details>
      </Card>
      </Disclosure>

      <Disclosure title="Local signals" summary={rec.facility.demo_origin_id ? 'Local road and closure sources: not available for this location' : `${rec.evidence.filter(e => e.kind === 'local_fact').length} local facts · ${rec.evidence.filter(e => e.kind === 'local_fact' && (e.value as Record<string, unknown>)?.relevance === 'access').length} on access routes`}>
      <LocalSignals evidence={rec.evidence} tz={tz} />
      </Disclosure>
      {!rec.facility.temporary && <Disclosure title="Map" summary="Facility, access routes, commute bands and local facts"><MapView recId={rec.id} title="Map: facility, access routes, commute bands, local facts" /></Disclosure>}

      <AirQuality evidence={rec.evidence} tz={tz} targetDay={rec.operating_date} />
      <Disclosure title="Evidence" summary={`${rec.evidence.length} sources · ${rec.evidence.every(e => e.status === 'ok' && (!e.valid_until || new Date(e.valid_until).getTime() > Date.now())) ? 'all current' : 'missing or stale sources; see details'}`}>
      <EvidenceList evidence={rec.evidence} tz={tz} />
      </Disclosure>
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

export function NearestThresholds({rec}: {rec: RecommendationDetail}) {
  const [expanded, setExpanded] = useState(false);
  const rows = rec.what_would_change.map((text, index) => {
    const aqi = text.match(/AQI (unknown|\d+) \/ (\d+)/);
    const threshold = text.match(/Gusts reaching ([\d.]+) mph/i);
    const observed = text.match(/forecast peak is ([\d.]+) mph/i);
    return {text, index, metric: aqi ? 'AQI' : 'Gusts', units: aqi ? '' : ' mph',
      threshold: aqi ? Number(aqi[2]) : threshold ? Number(threshold[1]) : null,
      observed: aqi ? (aqi[1] === 'unknown' ? null : Number(aqi[1])) : observed ? Number(observed[1]) : null};
  }).sort((a, b) => (a.threshold != null && a.observed != null ? Math.abs(a.threshold - a.observed) : Infinity)
    - (b.threshold != null && b.observed != null ? Math.abs(b.threshold - b.observed) : Infinity));
  if (!rows.length) return <p className="text-sm text-slate-600">No nearer escalation threshold. Recheck if conditions change.</p>;
  return <div className="space-y-3 text-sm">
    {(expanded ? rows : rows.slice(0, 2)).map(row => <div key={row.index}>
      {row.threshold != null && <><div className="font-semibold">{row.metric} {row.observed ?? "unavailable"} / {row.threshold}{row.units}</div>
        <div className="threshold-progress" role="progressbar" aria-label={`${row.metric} toward ${row.threshold}${row.units}`}
          aria-valuemin={0} aria-valuemax={row.threshold} aria-valuenow={row.observed == null ? undefined : Math.min(row.observed, row.threshold)}>
          <span style={{width: `${row.observed == null ? 0 : Math.min(100, 100 * row.observed / row.threshold)}%`}} /></div></>}
      <p className="mt-1">{humanText(row.text)} <Link className="underline" to={`/thresholds#${rec.change_rule_ids?.[row.index] ?? 'nws_cap'}`}>Threshold</Link></p>
    </div>)}
    {rows.length > 2 && <button className="text-sm underline" onClick={() => setExpanded(!expanded)}>{expanded ? "Show nearest two" : "Show all"}</button>}
  </div>;
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
                <td className="py-2 pr-3">
                  {{nws: "NWS", nwps: "NOAA river", modot: "MoDOT", roster: "Schedule", demo: "Demo scenario", local: "Local report", human: "Staff report", sweep: 'Evidence refresh', airnow: 'EPA AirNow'}[e.source]}
                  {e.scenario_kind && <div className="text-xs font-bold">{e.scenario_kind === "simulated" ? "SIMULATED" : "REPLAY"}</div>}
                  {e.source === 'human' && <div className="mt-1 rounded bg-purple-100 px-2 py-1 text-xs normal-case text-purple-900">Human reported {(e.value as {source_name?: string})?.source_name}{(e.value as {simulated?: boolean})?.simulated ? ' · SIMULATED for the demo' : ''}</div>}
                  {isLocal(e) && !e.scenario_kind && <div className="mt-1"><AuthorityBadge authority={(e.value as { authority: string }).authority} /></div>}
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
                <td className="py-2 text-xs">
                  <SavedCopyLinks evidence={e} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
