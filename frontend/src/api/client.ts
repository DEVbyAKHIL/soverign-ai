// Tokens are held in memory only (never localStorage) to limit XSS exposure.
let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(base: string, path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const res = await fetch(`${base}${path}`, { ...init, headers, credentials: "omit" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, body?.error?.code ?? "unknown", body?.error?.message ?? res.statusText);
  }
  return body as T;
}

/** Gateway API (decisions, policies, audit ...). */
export const api = <T,>(path: string, init: RequestInit = {}) => request<T>("/api", path, init);
/** Reporting API (streaming aggregates). GET only. */
export const reporting = <T,>(path: string, signal?: AbortSignal) =>
  request<T>("/reporting", path, { signal });

export type Health = { status: string; version: string };
export const getHealth = () => api<Health>("/healthz");

// Demo tenant for the development login. Production tokens come from the OIDC provider.
export const DEMO_TENANT = "aaaaaaaa-0000-4000-8000-000000000001";
export type Session = { subject: string; tenant: string; role: string };

export async function devLogin(role: string): Promise<Session> {
  const subject = crypto.randomUUID();
  const r = await api<{ access_token: string }>("/v1/auth/dev-token", {
    method: "POST",
    body: JSON.stringify({ subject, tenant_id: DEMO_TENANT, role }),
  });
  setAccessToken(r.access_token);
  return { subject, tenant: DEMO_TENANT, role };
}

export type DayPoint = {
  day: string; allow: number; allow_with_modification: number; deny: number; review: number;
  avg_risk: number;
};
export type Summary = {
  days: number; total: number; by_decision: Record<string, number>; pending_reviews: number;
  avg_risk: number; timeline: DayPoint[]; top_policies: Record<string, number>;
  entity_types: Record<string, number>; intents: Record<string, number>;
  model_responses: Record<string, number>; output_guard: Record<string, number>;
  agents: { agent_id: string; name: string; risk_score: number; agent_type: string }[];
};
export const getSummary = (days: number) => api<Summary>(`/v1/dashboard/summary?days=${days}`);

export type DecisionRow = {
  decision_id: string; created_at: string; final_decision: string; action_taken: string;
  risk_score: number; intent_label: string; destination_model: string;
  destination_jurisdiction: string; review_status: string; policy_ids: string[];
  agent_id: string | null;
};
export const listDecisions = (q = "") => api<DecisionRow[]>(`/v1/decisions?limit=100${q}`);
export const reviewDecision = (id: string, outcome: "approve" | "reject", reason: string) =>
  api(`/v1/reviews/${id}/decision`, { method: "POST", body: JSON.stringify({ outcome, reason }) });

export type AuditEvent = {
  id: string; seq: number; decision_id: string; event_type: string; actor_id: string;
  event_data: Record<string, unknown>; previous_hash: string; event_hash: string; created_at: string;
};
export const listAudit = () => api<AuditEvent[]>("/v1/audit/events?limit=100");
export const verifyAudit = () =>
  api<{ valid: boolean; events_checked: number; first_invalid_seq: number | null; reason: string | null }>(
    "/v1/audit/verify",
  );

export type Policy = {
  policy_id: string; version: string; name: string; description: string; severity: string;
  actions: string[];
};
export const listPolicies = () =>
  api<{ policy_set_version: string; policies: Policy[] }>("/v1/policies");

export type Agent = {
  id: string; name: string; agent_type: string; allowed_models: string[]; allowed_tools: string[];
  max_requests_per_minute: number; risk_score: number;
};
export const listAgents = () => api<Agent[]>("/v1/agents");

export type InferResult = {
  decision_id: string; final_decision: string; action_taken: string; risk_score: number;
  explanation: string; matched_policies: string[]; data_classification: string;
  review_status: string;
  intent: { label: string; confidence: number; risk_score: number; signals: string[] };
  detections: { entity_type: string; confidence: number; detector: string; risk_level: string }[];
  transformed_content: Record<string, unknown> | null;
  model_response: {
    status: string; reason?: string; model?: string; text?: string | null; latency_ms?: number;
    error?: string; output_guard?: { action: string; reasons: string[]; entity_types: string[] };
  } | null;
};
export const infer = (body: Record<string, unknown>) =>
  api<InferResult>("/v1/ai/infer", { method: "POST", body: JSON.stringify(body) });
