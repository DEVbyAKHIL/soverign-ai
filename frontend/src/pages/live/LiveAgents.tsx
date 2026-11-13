import { useState } from "react";
import { getAgentRisk, getAgents } from "../../api/reporting";
import { LiveHeader, SeriesChart, pct, shortId, type Row } from "../../components/live";
import { usePolling } from "../../hooks/usePolling";

export default function LiveAgents() {
  const [minutes, setMinutes] = useState(60);
  const [selected, setSelected] = useState<string | null>(null);
  const list = usePolling((s) => getAgents(minutes, s), [minutes]);
  const agents = list.data?.items ?? [];
  const agent = selected ?? agents[0]?.agent_id ?? null;
  const hist = usePolling(
    (s) => (agent ? getAgentRisk(agent, minutes, s) : Promise.resolve([])), [agent, minutes],
  );
  const rows: Row[] = (hist.data ?? []).map((h) => ({
    t: new Date(h.window_start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
    ts: h.window_start, anomaly_score: h.anomaly_score, deny_rate: h.deny_rate, injection_rate: h.injection_rate,
  }));
  return (
    <section>
      <LiveHeader title="Agent risk" minutes={minutes} setMinutes={setMinutes}
        updatedAt={list.updatedAt} error={list.error ?? hist.error} />
      <div className="grid2">
        <div className="card">
          <h3>Agents by anomaly score (latest 10-min window)</h3>
          {agents.length === 0 ? <p className="muted">No agent activity in this range.</p> : (
            <div style={{ overflowX: "auto" }}><table>
              <thead><tr><th>Agent</th><th>Score</th><th>Requests</th><th>Deny</th><th>Injection</th><th>PII</th><th>Non-EU dest.</th></tr></thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.agent_id} onClick={() => setSelected(a.agent_id)} style={{ cursor: "pointer" }}
                    className={a.agent_id === agent ? "selected" : undefined} aria-selected={a.agent_id === agent}>
                    <td className="mono" title={a.agent_id}>{shortId(a.agent_id)}</td>
                    <td className={a.anomaly_score >= 0.5 ? "bad" : undefined}>{a.anomaly_score.toFixed(2)}</td>
                    <td>{a.requests}</td><td>{pct(a.deny_rate)}</td><td>{pct(a.injection_rate)}</td>
                    <td>{pct(a.pii_rate)}</td><td>{pct(a.risky_destination_share)}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </div>
        <SeriesChart title={agent ? `Agent ${shortId(agent)} over time (10-min sliding)` : "Agent history"} rows={rows}
          keys={["anomaly_score", "deny_rate", "injection_rate"]} yFormat={(v) => v.toFixed(2)}
          labelOf={(k) => ({ anomaly_score: "Anomaly score", deny_rate: "Deny rate", injection_rate: "Injection rate" }[k] ?? k)}
          note="Score = transparent heuristic over deny, injection, PII and non-EU shares (docs/aggregations.md)." />
      </div>
    </section>
  );
}
