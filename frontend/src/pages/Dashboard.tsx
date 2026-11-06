import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getSummary, type Summary } from "../api/client";
import { BarList, DECISION_META, DECISION_ORDER, ErrorNote, Stat } from "../components/ui";

const RANGES = [1, 7, 30, 90];

export default function Dashboard() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [table, setTable] = useState(false);

  useEffect(() => {
    getSummary(days).then((s) => { setData(s); setError(null); }).catch((e: Error) => setError(e.message));
  }, [days]);

  const blocked = data ? (data.by_decision.deny ?? 0) : 0;
  const guarded = data ? Object.values(data.output_guard).reduce((a, b) => a + b, 0) : 0;

  return (
    <section>
      <div className="toolbar">
        <h2>Overview</h2>
        <div className="segmented" role="group" aria-label="Time range">
          {RANGES.map((d) => (
            <button key={d} className={d === days ? "on" : ""} onClick={() => setDays(d)}>
              {d === 1 ? "24 h" : `${d} d`}
            </button>
          ))}
        </div>
      </div>
      <ErrorNote error={error} />
      {data && (
        <>
          <div className="stats">
            <Stat label="Requests governed" value={data.total} />
            <Stat label="Denied" value={blocked}
              note={data.total ? `${Math.round((blocked / data.total) * 100)}% of requests` : undefined} />
            <Stat label="Awaiting review" value={data.pending_reviews} />
            <Stat label="Average input risk" value={data.avg_risk.toFixed(2)} note="0 = none, 1 = maximum" />
            <Stat label="Model answers guarded" value={guarded}
              note={`${data.output_guard.redacted ?? 0} redacted · ${data.output_guard.blocked ?? 0} blocked`} />
          </div>

          <div className="card">
            <div className="toolbar">
              <h3>Decisions per day</h3>
              <button className="link" onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button>
            </div>
            {data.timeline.length === 0 ? <p className="muted">No requests in this period.</p> : table ? (
              <table>
                <thead><tr><th>Day</th>{DECISION_ORDER.map((k) => <th key={k}>{DECISION_META[k].label}</th>)}<th>Avg risk</th></tr></thead>
                <tbody>
                  {data.timeline.map((p) => (
                    <tr key={p.day}><td>{p.day}</td>{DECISION_ORDER.map((k) => <td key={k}>{p[k]}</td>)}<td>{p.avg_risk.toFixed(2)}</td></tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={data.timeline} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid vertical={false} stroke="var(--border)" />
                  <XAxis dataKey="day" tick={{ fill: "var(--muted)", fontSize: 12 }} stroke="var(--border)" />
                  <YAxis allowDecimals={false} tick={{ fill: "var(--muted)", fontSize: 12 }} stroke="var(--border)" />
                  <Tooltip cursor={{ fill: "var(--hover)" }}
                    contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }} />
                  <Legend formatter={(v) => <span style={{ color: "var(--text)" }}>{v}</span>} />
                  {DECISION_ORDER.map((k, i) => (
                    <Bar key={k} dataKey={k} name={DECISION_META[k].label} stackId="d" fill={DECISION_META[k].color}
                      stroke="var(--panel)" strokeWidth={2} maxBarSize={36}
                      radius={i === DECISION_ORDER.length - 1 ? [4, 4, 0, 0] : 0} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className="grid">
            <BarList title="Sensitive data detected" data={data.entity_types} empty="No sensitive data detected." />
            <BarList title="Policies triggered" data={data.top_policies} empty="No policies matched." />
            <BarList title="Intent of requests" data={data.intents} empty="No requests." />
            <div className="card">
              <h3>Riskiest agents</h3>
              {data.agents.length === 0 ? <p className="muted">No agents registered.</p> : (
                <table>
                  <thead><tr><th>Agent</th><th>Type</th><th>Risk</th></tr></thead>
                  <tbody>
                    {data.agents.map((a) => (
                      <tr key={a.agent_id}>
                        <td>{a.name}</td><td className="muted">{a.agent_type}</td>
                        <td className={a.risk_score >= 0.5 ? "bad" : ""}>{a.risk_score.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
