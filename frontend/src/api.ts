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
  access_routes: string[];
  temporary: boolean;
}

export interface Evidence {
  id: string;
  facility_id: string | null;
  evaluation_id: string | null;
  source: "nws" | "nwps" | "modot" | "roster";
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
  margin: number | null;
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
}

export interface RecommendationDetail extends Recommendation {
  facility: Facility;
  evidence: Evidence[];
  decision: Decision | null;
  decide_by: string | null;
}

export interface DecisionIn {
  manager_name: string;
  action: "approve" | "override";
  final_posture?: Posture;
  reason?: string;
}

export interface Recipient {
  employee_id: string;
  name: string;
  included: boolean;
  reason: string;
  channel: "push" | "email";
  contact: string;
  language: "en" | "es";
  essential: boolean;
  early_notice: boolean;
  typical_departure_time: string;
  message: string | null;
}

export interface Notification {
  id: string;
  decision_id: string;
  employee_id: string;
  included: boolean;
  reason: string;
  channel: string;
  message: string | null;
  sent_at: string | null;
  delivered: boolean;
  acknowledged_at: string | null;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
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
  health: () => request<{ status: string }>("/health"),
  facilities: () => request<Facility[]>("/facilities"),
  facility: (id: string) => request<Facility>(`/facilities/${id}`),
  latestRecommendation: (facilityId: string) =>
    request<RecommendationDetail | null>(`/facilities/${facilityId}/latest-recommendation`),
  evaluate: (facilityId: string, evaluationId?: string) =>
    post<RecommendationDetail>(`/facilities/${facilityId}/evaluate`, { evaluation_id: evaluationId }),
  evaluatePoint: (lat: number, lon: number, name: string, evaluationId?: string) =>
    post<RecommendationDetail>("/evaluate-point", { lat, lon, name, evaluation_id: evaluationId }),
  agentActivity: (p: { evaluation_id?: string; since?: string; facility_id?: string }) =>
    request<RecordEvent[]>(`/agent-activity${qs(p)}`),
  recommendation: (id: string) => request<RecommendationDetail>(`/recommendations/${id}`),
  decide: (recommendationId: string, body: DecisionIn) =>
    post<Decision>(`/recommendations/${recommendationId}/decision`, body),
  recipients: (decisionId: string) => request<Recipient[]>(`/decisions/${decisionId}/recipients`),
  notifications: (decisionId: string) => request<Notification[]>(`/decisions/${decisionId}/notifications`),
  notify: (decisionId: string) => post<Notification[]>(`/decisions/${decisionId}/notify`),
  nationalScan: () => request<NationalAlert[]>("/national-scan"),
  record: (p: { facility_id?: string; since?: string; until?: string; limit?: number; newest_first?: boolean } = {}) =>
    request<RecordEvent[]>(`/record${qs(p)}`),
  recordAt: (facilityId: string, timestamp: string) =>
    request<RecordAt>(`/record/at${qs({ facility_id: facilityId, timestamp })}`),
};
