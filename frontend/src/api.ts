// Typed client for the FastAPI backend. Types mirror backend/app/schemas.py.

export type Posture = "normal" | "delayed_arrival" | "early_dismissal" | "telework" | "closure";
export const POSTURES: Posture[] = ["normal", "delayed_arrival", "early_dismissal", "telework", "closure"];
export type Confidence = "high" | "medium" | "low";
export type EvidenceStatus = "ok" | "unknown" | "stale";

export interface Facility {
  id: string;
  name: string;
  address: string;
  lat: number;
  lon: number;
  timezone: string;
  open_time: string;
  close_time: string;
  access_routes: AccessSegment[];
  operating_days: number[] | null;
  temporary: boolean;
}

export interface AccessSegment {
  route: string;
  name: string;
  points: [number, number][];
  buffer_km: number;
  ramps: boolean;
}

export interface Evidence {
  id: string;
  facility_id: string | null;
  evaluation_id: string | null;
  source: "nws" | "nwps" | "modot" | "roster" | "demo" | "local";
  kind: string;
  value: unknown;
  units: string | null;
  source_time: string | null;
  retrieved_at: string;
  valid_until: string | null;
  url: string | null;
  snapshot_hash: string | null;
  relevance_note: string;
  ambiguity_flags: string[];
  status: EvidenceStatus;
}

export interface Trigger {
  rule_id: string;
  description: string;
  threshold: number | string | null;
  observed: number | string | null;
  margin: number | string | null; // e.g. "1 of 3 access routes closed"
  units: string | null;
  citation: string;
  posture: Posture;
  evidence_id: string | null;
}

export interface Decision {
  id: string;
  recommendation_id: string;
  manager_name: string;
  action: "approve" | "override";
  final_posture: Posture;
  reason: string | null;
  decided_at: string;
  delay_hours: number | null;
  dismissal_time: string | null;
}

export interface Recommendation {
  id: string;
  facility_id: string;
  evaluation_id: string | null;
  evaluated_at: string;
  operating_date: string;
  posture: Posture;
  triggers: Trigger[];
  confidence: Confidence;
  confidence_reasons: string[];
  what_would_change: string[];
  rationale: string;
  rationale_source: "agent" | "template";
  evidence_ids: string[];
  policy_version: string;
  target_mode: "next_operating_day" | "explicit" | "demo_workday" | "scheduled" | null;
  target_note: string | null;
  lead_time_hours: number | null;
  rationale_note: string | null; // "AI draft" | "AI draft rejected: <reason> - template used" | "LLM unavailable - template used"
}

export interface DecisionClock {
  at: string | null;
  employee_id: string | null;
  label: string;
}

export interface BackupCandidate {
  employee_id: string;
  name: string;
  designation: Designation;
  commute_minutes: number;
  scheduled: string;
}

export interface CoverageItem {
  code: string;
  name: string;
  required: number;
  available: string[];
  exposed: string[];
  called_in: { employee_id: string; name: string; notification_id: string; acknowledged: boolean }[];
  backups: BackupCandidate[];
  gap: number;
  status: "covered" | "pending" | "shortfall";
  reason_code: "STAFFING_COVERED" | "STAFFING_SHORTFALL";
  label: string;
}

export interface PeopleSummary {
  posture: Posture;
  params: Record<string, string | number>;
  clock: DecisionClock;
  exposed_count: number;
  coverage: CoverageItem[];
  counts_by_code: Record<string, number>;
  action_required: number;
  fyi: number;
}

export interface RecommendationDetail extends Recommendation {
  facility: Facility;
  evidence: Evidence[];
  decision: Decision | null;
  decide_by: string | null;
  people: PeopleSummary | null;
  next_reevaluation: string | null;
}

/** Default: the next operating day. target_date: an explicit day. demo_workday: DEMO_MODE only. */
export interface EvaluateTarget {
  target_date?: string;
  demo_workday?: boolean;
}

export interface DecisionIn {
  manager_name: string;
  action: "approve" | "override";
  final_posture?: Posture;
  reason?: string;
  delay_hours?: number;
  dismissal_time?: string;
}

export type Role = "employee" | "supervisor" | "facility_manager" | "facility_ops" | "security_desk" | "contractor";
export type Designation = "emergency" | "telework_ready" | "non_telework";
export type MessageType =
  | "action_required" | "fyi" | "supervisor_summary" | "access_list" | "staffing_alert" | "call_in" | "none";

export interface EmployeePublic {
  id: string;
  name: string;
  role: Role;
  designation: Designation;
  supervisor_id: string | null;
  functions: string[];
  primary_function: string | null;
  accommodation_on_file: boolean;
  language: "en" | "es";
  preferred_channels: ("push" | "email")[];
  commute_minutes: number;
  access_level: string;
}

export interface ChannelResult {
  sent_at: string | null;
  ok: boolean;
  error: string | null;
  fallback: boolean;
}

export interface Notification {
  id: string;
  notice_id: string;
  employee_id: string;
  included: boolean;
  reason_codes: string[];
  reason_text: string;
  audience_text: string;
  duty_instruction: string;
  building_access: string;
  access_code: string;
  notify_by: string | null;
  wave: "1" | "2" | "fyi";
  message_type: MessageType;
  change: "initial" | "update" | "all_clear" | "unchanged";
  requires_ack: boolean;
  function_code: string | null;
  channels_planned: string[];
  channel_results: Record<string, ChannelResult>;
  subject: string | null;
  message: string | null;
  drafted_by: string | null;
  sent_at: string | null;
  acknowledged_at: string | null;
  reminder_sent_at: string | null;
  escalation_level: number;
  escalated_to: string | null;
}

export interface Notice {
  id: string;
  decision_id: string;
  facility_id: string;
  operating_date: string;
  version: number;
  supersedes_notice_id: string | null;
  kind: "initial" | "update" | "all_clear";
  posture: Posture;
  params: Record<string, string | number>;
  created_at: string;
  sent_at: string | null;
}

export interface NoticeDetail {
  notice: Notice;
  previous: Notice | null;
  clock: DecisionClock;
  coverage: CoverageItem[];
  notifications: Notification[];
  names: Record<string, string>;
}

export interface AccessRow {
  employee_id: string;
  name: string;
  role: Role;
  access_level: string;
  access: string;
  window: string;
  code: string;
  reason: string;
  scheduled: string;
}

export interface AccessList {
  facility_id: string;
  facility_name: string;
  operating_date: string;
  posture: Posture;
  notice_id: string | null;
  policy: string;
  rows: AccessRow[];
}

export interface TeamNotification {
  notification_id: string;
  employee_id: string;
  name: string;
  message_type: MessageType;
  included: boolean;
  audience_text: string;
  duty_instruction: string;
  building_access: string;
  wave: string;
  notify_by: string | null;
  channels_planned: string[];
  delivered: boolean | null;
  sent_at: string | null;
  requires_ack: boolean;
  acknowledged_at: string | null;
  escalation_level: number;
}

export interface TeamBoard {
  supervisor: EmployeePublic;
  notice: Notice | null;
  members: EmployeePublic[];
  notifications: TeamNotification[];
  suggestions: { employee_id: string; name: string; audience_text: string; suggestion: string }[];
}

export interface MyStatus {
  employee: EmployeePublic;
  facility_name: string;
  operating_date: string | null;
  posture: Posture | null;
  notification: Notification | null;
  duty_instruction: string;
  building_access: string;
  ack_url: string | null;
}

export type Authority = "official_federal" | "official_state" | "official_local" | "news";

/** Evidence.value for kind "local_fact" (validated in code; see backend agents/local/facts.py). */
export interface LocalFactValue {
  item_id: string;
  fact_type: string;
  location_text: string;
  routes_mentioned: string[];
  counties: string[];
  effective_from: string | null;
  effective_until: string | null;
  matches_facility_access_route: boolean;
  matches_employee_commute_routes: string[];
  supporting_quote: string;
  confidence: "high" | "medium" | "low";
  relevance: "facility" | "commute" | "not_relevant" | "uncertain";
  distance_km: number | null;
  segments: string[];
  affected_employee_ids: string[];
  agent: "roads" | "schools" | "public_safety" | "news";
  source_id: string;
  source_name: string;
  authority: Authority;
  title: string;
  link: string;
  reported_at: string | null;
  extracted_by: "agent" | "code";
  category: "construction" | "incident" | "flood" | "other" | null;
  active_since: string | null;
}

export interface SourceRow {
  id: string;
  name: string;
  url: string;
  link: string | null;
  kind: string;
  agent: string;
  authority: Authority;
  area: { counties?: string[]; cities?: string[] };
  refresh_minutes: number;
  robots: string;
  last_fetch: string | null;
  status: string;
  items_found: number | null;
  error: string | null;
}

export interface SourcesResponse {
  sources: SourceRow[];
  integrated: { id: string; name: string; url: string; authority: string; note?: string }[];
  skipped: { name: string; url: string; reason: string }[];
}

export interface Persona {
  employee_id: string;
  name: string;
  role: Role;
  ntfy_topic: string | null;
  description: string;
}

export interface Viewers {
  supervisors: EmployeePublic[];
  security_desk: EmployeePublic[];
  employees: EmployeePublic[];
  personas: Persona[];
}

export interface RecordEvent {
  id: number;
  timestamp: string;
  facility_id: string | null;
  evaluation_id: string | null;
  event_type: string;
  payload: Record<string, unknown>;
}

export interface NationalAlert {
  id: string;
  event: string;
  severity: string;
  certainty: string;
  urgency: string;
  onset: string | null;
  ends: string | null;
  headline: string | null;
  area_desc: string | null;
  sent: string | null;
  lat: number | null;
  lon: number | null;
}

export interface RecordAt {
  as_of: string;
  facility_id: string;
  recommendation: RecommendationDetail | null;
  notifications: Notification[];
}

/** Client-chosen evaluation id, so the UI can poll agent activity while the evaluation runs. */
export function newEvaluationId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID().replace(/-/g, "");
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const NEEDS_PERSON_EVENT = "closurecall:needs-person";
export const NEEDS_PERSON_MESSAGE = "Choose who you're viewing as";

// Demo role switcher (no real auth): every request says who is looking. The server filters.
let viewerHeaders: Record<string, string> = {};
export function setViewer(role: string, personId?: string | null) {
  viewerHeaders = { "X-Role": role, ...(personId ? { "X-Person": personId } : {}) };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...viewerHeaders, ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
      else if (Array.isArray(body.detail)) msg = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* not JSON */
    }
    if (res.status === 400 && msg.includes("X-Person")) {
      // Demo role switcher: a role that needs a person has none yet. Ask, don't alarm.
      window.dispatchEvent(new Event(NEEDS_PERSON_EVENT));
      throw new ApiError(res.status, NEEDS_PERSON_MESSAGE);
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  health: () => request<{ status: string; demo_mode: boolean }>("/health"),
  facilities: () => request<Facility[]>("/facilities"),
  facility: (id: string) => request<Facility>(`/facilities/${id}`),
  latestRecommendation: (facilityId: string) =>
    request<RecommendationDetail | null>(`/facilities/${facilityId}/latest-recommendation`),
  evaluate: (facilityId: string, evaluationId?: string, target: EvaluateTarget = {}) =>
    post<RecommendationDetail>(`/facilities/${facilityId}/evaluate`, { evaluation_id: evaluationId, ...target }),
  evaluatePoint: (lat: number, lon: number, name: string, evaluationId?: string) =>
    post<RecommendationDetail>("/evaluate-point", { lat, lon, name, evaluation_id: evaluationId }),
  agentActivity: (p: { evaluation_id?: string; since?: string; facility_id?: string }) =>
    request<RecordEvent[]>(`/agent-activity${qs(p)}`),
  recommendation: (id: string) => request<RecommendationDetail>(`/recommendations/${id}`),
  decide: (recommendationId: string, body: DecisionIn) =>
    post<Decision>(`/recommendations/${recommendationId}/decision`, body),
  decisionNotice: (decisionId: string) => request<NoticeDetail>(`/decisions/${decisionId}/notice`),
  notice: (noticeId: string) => request<NoticeDetail>(`/notices/${noticeId}`),
  notifications: (decisionId: string) => request<Notification[]>(`/decisions/${decisionId}/notifications`),
  notify: (decisionId: string) => post<Notification[]>(`/decisions/${decisionId}/notify`),
  callIn: (noticeId: string, body: { employee_id: string; function_code: string; manager_name: string }) =>
    post<Notification>(`/notices/${noticeId}/call-ins`, body),
  accessList: (facilityId: string) => request<AccessList>(`/facilities/${facilityId}/access-list`),
  team: (supervisorId?: string) => request<TeamBoard>(`/team${qs({ supervisor_id: supervisorId })}`),
  me: () => request<MyStatus>("/me"),
  ack: (notificationId: string) => post<Notification>(`/notifications/${notificationId}/ack`),
  viewers: () => request<Viewers>("/viewers"),
  sources: () => request<SourcesResponse>("/sources"),
  simulate: (body: { kind: "zone_alert" | "road_closure" | "clear"; zone?: string; route?: string }) =>
    post<Record<string, unknown>>("/demo/simulate", body),
  nationalScan: () => request<NationalAlert[]>("/national-scan"),
  record: (p: { facility_id?: string; since?: string; until?: string; limit?: number; newest_first?: boolean } = {}) =>
    request<RecordEvent[]>(`/record${qs(p)}`),
  recordAt: (facilityId: string, timestamp: string) =>
    request<RecordAt>(`/record/at${qs({ facility_id: facilityId, timestamp })}`),
};
