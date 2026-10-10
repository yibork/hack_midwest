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

export const codeLabel = (c: string) => CODE_LABEL[c] ?? c.replace(/_/g, " ").toLowerCase();

const COVERAGE_STYLE: Record<CoverageItem["status"], string> = {
  covered: "border-green-300 bg-green-50 text-green-900",
  pending: "border-amber-300 bg-amber-50 text-amber-900",
  shortfall: "border-red-400 bg-red-50 text-red-900",
};

/** "Animal care 1/2 - shortfall" chips. */
export function CoverageStrip({ coverage }: { coverage: CoverageItem[] }) {
  if (!coverage.length) return null;
  return (
    <div className="flex flex-wrap gap-2" aria-label="Critical function staffing">
      {coverage.map((c) => (
        <span key={c.code} className={`rounded border px-2 py-1 text-sm font-semibold ${COVERAGE_STYLE[c.status]}`}
              title={[c.available.length ? `Available: ${c.available.join(", ")}` : "Nobody available",
                      c.exposed.length ? `Exposed commute: ${c.exposed.join(", ")}` : ""].filter(Boolean).join("\n")}>
          {c.status === "shortfall" ? "▲ " : c.status === "pending" ? "… " : "✓ "}{c.label}
        </span>
      ))}
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
        <span className={people.exposed_count ? "font-semibold text-amber-800" : ""}>
          <strong>{people.exposed_count}</strong> with an exposed commute
        </span>
        {Object.entries(people.params).filter(([k]) => k !== "posture").map(([k, v]) => (
          <span key={k} className="text-slate-500">{k.replace(/_/g, " ")}: {v}</span>
        ))}
      </div>
    </div>
  );
}
