import { getOverview, getSeries } from "../../api/reporting";
import { BarList, Stat } from "../../components/ui";
import { EVENT_DECISIONS, LiveHeader, SeriesChart, ms, pct, pivot, shortId } from "../../components/live";
import { usePolling } from "../../hooks/usePolling";

export default function LiveOverview() {
  const o = usePolling((s) => getOverview(s));
  const req = usePolling((s) => getSeries("requests", 60, { metric: "requests" }, s));
  const d = o.data;
  const { rows, keys } = pivot(req.data?.items ?? [], () => "requests");
  const decisions = Object.fromEntries(
    Object.entries(d?.decisions_last_5m ?? {}).map(([k, v]) => [EVENT_DECISIONS[k]?.label ?? k, v]),
  );
  return (
    <section>
      <LiveHeader title="Real-time overview" updatedAt={o.updatedAt} asOf={d?.as_of} error={o.error ?? req.error} />
      {d && (
        <>
          <div className="stats">
            <Stat label="Requests (last 5 min)" value={d.requests_last_5m.toLocaleString()}
              note={`${d.requests_per_minute.toLocaleString()} per minute`} />
            <Stat label="Deny rate" value={pct(d.deny_rate)} note="5-min sliding window" />
            <Stat label="P99 latency" value={ms(d.p99_latency_ms)} note={`average ${ms(d.avg_latency_ms)}`} />
            <Stat label="Failed evaluations" value={d.failed_evaluations ?? "–"} note="5-min sliding window" />
            <Stat label="PII entities" value={d.pii_entities_last_window ?? "–"} note="latest 5-min window" />
            <Stat label="Injection attempts" value={d.injection_attempts ?? "–"} note="agents, 5-min window" />
          </div>
          <div className="grid2">
            <SeriesChart title="Requests per minute (last hour)" rows={rows} keys={keys} kind="bar"
              colorOf={() => "var(--series-1)"} labelOf={() => "Requests"} />
            <BarList title="Decisions (last 5 min)" data={decisions} empty="No decisions yet." />
          </div>
          <div className="card">
            <h3>Riskiest agents (latest 10-min window)</h3>
            {d.top_risky_agents.length === 0 ? <p className="muted">No agent activity.</p> : (
              <table>
                <thead><tr><th>Agent</th><th>Anomaly score</th><th>Requests</th><th>Deny rate</th><th>Injection rate</th><th>Window end</th></tr></thead>
                <tbody>
                  {d.top_risky_agents.map((a) => (
                    <tr key={a.agent_id}>
                      <td className="mono" title={a.agent_id}>{shortId(a.agent_id)}</td>
                      <td>{a.anomaly_score.toFixed(2)}</td><td>{a.requests}</td>
                      <td>{pct(a.deny_rate)}</td><td>{pct(a.injection_rate)}</td>
                      <td>{new Date(a.window_end).toLocaleTimeString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <p className="muted small">
            Processor last batch: {d.pipeline_last_batch_at ? new Date(d.pipeline_last_batch_at).toLocaleTimeString() : "never"}
          </p>
        </>
      )}
    </section>
  );
}
