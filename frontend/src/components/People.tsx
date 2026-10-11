import type { CoverageItem, PeopleSummary } from "../api";

export const CODE_LABEL: Record<string, string> = {
  DETAIL_OUT: "On detail elsewhere",
  DETAIL_IN: "On detail here",
  LEAVE: "On leave",
  DAY_OFF: "Day off",
  TELEWORK_SCHEDULED: "Already teleworking",
  SHIFT_NOT_AFFECTED: "Shift not affected",
  EMERGENCY_DUTY: "Emergency duty",
  FACILITY_OPS: "Facility ops",
  CONTRACTOR_STATUS_ONLY: "Contractor (status only)",
  SUPERVISOR_SUMMARY: "Supervisor summary",
  SECURITY_ACCESS_LIST: "Security access list",
  SCHEDULED_ONSITE: "Scheduled onsite",
  HOME_IN_WARNING_AREA: "Home in warning area",
  ROUTE_CLOSED: "Route closed",
  ACCOMMODATION_ON_FILE: "Accommodation on file",
  ALL_CLEAR: "All clear",
  CALL_IN: "Call-in",
  STAFFING_SHORTFALL: "Staffing shortfall",
  EXPOSURE_SUGGESTION: "Exposure suggestion",
};

const EXPOSURE_RULE =
  "Exposure counts only road closures on a commute route (inside the home-to-facility corridor) and " +
  "weather warnings at home. Ramp-only closures, lane closures and delays are minor: shown, not counted.";

export const codeLabel = (c: string) => CODE_LABEL[c] ?? c.replace(/_/g, " ").toLowerCase();

const COVERAGE_STYLE: Record<CoverageItem["status"], string> = {
  covered: "border-slate-200 bg-white text-slate-700",
  pending: "border-amber-300 bg-amber-50 text-amber-900",
  shortfall: "border-red-400 bg-red-50 text-red-900",
};

/** "Animal care 1/2 - shortfall" chips. */
export function CoverageStrip({ coverage }: { coverage: CoverageItem[] }) {
  if (!coverage.length) return null;
  const issues = coverage.filter(c => c.status !== "covered");
  return (
    <div aria-label="Critical function staffing">
      {!issues.length && <p className="text-sm text-slate-700">✓ All {coverage.length} critical functions covered</p>}
      <div className="flex flex-wrap gap-2">{issues.map((c) => (
        <span key={c.code} className={`rounded border px-2 py-1 text-sm font-semibold ${COVERAGE_STYLE[c.status]}`}
              title={[c.available.length ? `Available: ${c.available.join(", ")}` : "Nobody available",
                      c.exposed.length ? `Exposed commute: ${c.exposed.join(", ")}` : ""].filter(Boolean).join("\n")}>
          {c.status === "shortfall" ? "▲ " : c.status === "pending" ? "… " : "✓ "}{c.label}
        </span>
      ))}</div>
      <details className="mt-1 text-sm text-slate-600"><summary>Staffing details</summary>
        {coverage.map(c => <p className="mt-1" key={c.code}>{c.label} · {c.available.length ? c.available.join(", ") : "Nobody available"}</p>)}
      </details>
    </div>
  );
}

/** Coverage, exposure and who is affected, for the recommended posture. */
export function PeopleStrip({ people }: { people: PeopleSummary }) {
  return (
    <div className="space-y-2">
      <CoverageStrip coverage={people.coverage} />
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-700">
        <span><strong>{people.action_required}</strong> must act</span>
        <span><strong>{people.fyi}</strong> FYI</span>
        <span className={people.exposed_count ? "font-semibold text-amber-800" : ""} title={EXPOSURE_RULE}>
          <strong>{people.exposed_count}</strong> with an exposed commute
        </span>
        {people.minor_commute_count > 0 && (
          <span className="cursor-help text-slate-600 underline decoration-dotted" title={EXPOSURE_RULE}>
            <strong>{people.minor_commute_count}</strong> commute{people.minor_commute_count === 1 ? "" : "s"} pass
            {people.minor_commute_count === 1 ? "es" : ""} near {people.minor_commute_label} (minor: not counted as exposure)
          </span>
        )}
        {Object.entries(people.params).filter(([k]) => k !== "posture").map(([k, v]) => (
          <span key={k} className="text-slate-500">{k.replace(/_/g, " ")}: {v}</span>
        ))}
      </div>
    </div>
  );
}
