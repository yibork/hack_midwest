import { type ReactNode } from "react";
import { type RecommendationDetail } from "../api";
import { Card, ConfidenceBadge, PolicyDetails, fmtDateTime, humanText, postureLabel } from "./ui";

export default function RecommendationHero({rec, actions}: {rec: RecommendationDetail; actions?: ReactNode}) {
  const tz = rec.facility.timezone;
  const why = rec.triggers.length ? rec.triggers.map(t => `${humanText(t.description)}${t.observed != null ? ` (${humanText(String(t.observed))}${t.units ? ` ${t.units}` : ""})` : ""}`).slice(0, 2).join("; ") + "."
    : "No operating-status thresholds have been met for the target day.";
  return <Card className={`posture-hero posture-${rec.posture}`}>
    <div className="text-sm text-slate-600">Recommendation for {new Date(`${rec.operating_date}T12:00:00`).toLocaleDateString(undefined, {weekday: "long", month: "short", day: "numeric"})}</div>
    <div className="mt-2 hero-label">{postureLabel(rec.posture)}</div>
    <p className="mt-2">{why}</p>
    {rec.target_note && <p className="mt-1 text-sm text-slate-600">{humanText(rec.target_note)}</p>}
    <div className="hero-metrics">
      <div><span className="metric-label">Decision deadline</span><strong>{rec.decide_by ? fmtDateTime(rec.decide_by, tz) : "No action needed"}</strong></div>
      <div><span className="metric-label">Confidence</span><ConfidenceBadge confidence={rec.confidence} />
        <p className="mt-1 max-w-sm text-sm" title={humanText(rec.confidence_reasons?.[0])}>{humanText(rec.confidence_reasons?.[0]?.split(';')[0])}</p></div>
      <div><span className="metric-label">Lead time</span><strong>{rec.lead_time_hours == null ? "Not available" : rec.lead_time_hours < 0 ? "Commute window passed" : `${Math.round(rec.lead_time_hours)} h`}</strong></div>
      <div><span className="metric-label">Next re-evaluation</span><strong>{rec.next_reevaluation ? fmtDateTime(rec.next_reevaluation, tz) : "None scheduled"}</strong></div>
    </div>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3"><PolicyDetails version={rec.policy_version ?? "1.0.0"} />{actions}</div>
  </Card>;
}
