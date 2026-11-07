// Types and fetchers for the reporting API (reporting/schemas.py). Aggregates only: no raw events,
// prompts, outputs or entity values ever reach the browser.
import { reporting } from "./client";

export type MetricPoint = {
  window_start: string;
  window_end: string;
  window_type: "tumbling" | "sliding";
  dimensions: Record<string, string>;
  metric: string;
  value: number | null;
  event_count: number | null;
  suppressed: boolean;
};
export type MetricSeries = {
  tenant_id: string; from: string; to: string; as_of: string | null;
  items: MetricPoint[]; next_cursor: string | null;
};
export type AgentRisk = {
  agent_id: string; window_start: string; window_end: string; anomaly_score: number;
  requests: number; deny_rate: number | null; injection_rate: number | null;
  pii_rate: number | null; risky_destination_share: number | null;
};
export type Overview = {
  tenant_id: string; as_of: string | null; latest_window_end: string | null;
  requests_last_5m: number; requests_per_minute: number; decisions_last_5m: Record<string, number>;
  deny_rate: number | null; failed_evaluations: number | null; avg_latency_ms: number | null;
  p99_latency_ms: number | null; pii_entities_last_window: number | null;
  injection_attempts: number | null; top_risky_agents: AgentRisk[];
  pipeline_last_batch_at: string | null;
};

export type SeriesKind = "requests" | "policies" | "latency" | "pii" | "injections" | "sovereignty";

function qs(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  return q.toString();
}

/** Fetch every page of a series within the range (keyset pagination via next_cursor). */
export async function getSeries(
  kind: SeriesKind, minutes: number, params: Record<string, string | undefined> = {},
  signal?: AbortSignal,
): Promise<MetricSeries> {
  const from = new Date(Date.now() - minutes * 60_000).toISOString();
  let page = await reporting<MetricSeries>(`/v1/metrics/${kind}?${qs({ from, limit: 1000, ...params })}`, signal);
  const items = [...page.items];
  for (let i = 0; page.next_cursor && i < 20; i++) {
    page = await reporting<MetricSeries>(
      `/v1/metrics/${kind}?${qs({ from, limit: 1000, cursor: page.next_cursor, ...params })}`, signal,
    );
    items.push(...page.items);
  }
  return { ...page, items };
}

export const getOverview = (signal?: AbortSignal) => reporting<Overview>("/v1/metrics/overview", signal);
export const getAgents = (minutes: number, signal?: AbortSignal) =>
  reporting<{ items: AgentRisk[] }>(
    `/v1/metrics/agents?${qs({ from: new Date(Date.now() - minutes * 60_000).toISOString() })}`, signal,
  );
export const getAgentRisk = (agentId: string, minutes: number, signal?: AbortSignal) =>
  reporting<AgentRisk[]>(
    `/v1/agents/${encodeURIComponent(agentId)}/risk?${qs({ from: new Date(Date.now() - minutes * 60_000).toISOString() })}`,
    signal,
  );
