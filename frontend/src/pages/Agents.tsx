import { useEffect, useState } from "react";
import { listAgents, type Agent } from "../api/client";
import { ErrorNote } from "../components/ui";

export default function Agents() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { listAgents().then(setAgents).catch((e: Error) => setError(e.message)); }, []);

  return (
    <section>
      <h2>Agents</h2>
      <p className="muted">Registered AI agents, what they may use, and their long-running behaviour risk.</p>
      <ErrorNote error={error} />
      {agents && (
        <div className="card scroll">
          <table>
            <thead><tr><th>Agent</th><th>Type</th><th>Allowed models</th><th>Allowed tools</th><th>Limit / min</th><th>Risk</th></tr></thead>
            <tbody>
              {agents.length === 0 && <tr><td colSpan={6} className="muted">No agents registered.</td></tr>}
              {agents.map((a) => (
                <tr key={a.id}>
                  <td><strong>{a.name}</strong></td>
                  <td>{a.agent_type}</td>
                  <td className="small">{a.allowed_models.join(", ") || "—"}</td>
                  <td className="small">{a.allowed_tools.join(", ") || "—"}</td>
                  <td>{a.max_requests_per_minute}</td>
                  <td className={a.risk_score >= 0.5 ? "bad" : ""}>{a.risk_score.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
