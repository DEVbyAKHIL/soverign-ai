import { useCallback, useEffect, useState } from "react";
import { listDecisions, reviewDecision, type DecisionRow } from "../api/client";
import { DecisionBadge, ErrorNote } from "../components/ui";

const FILTERS = [
  { label: "All", q: "" },
  { label: "Awaiting review", q: "&review_status=pending" },
  { label: "Denied", q: "&final_decision=deny" },
  { label: "Modified", q: "&final_decision=allow_with_modification" },
];

export default function Decisions() {
  const [filter, setFilter] = useState(FILTERS[0].q);
  const [rows, setRows] = useState<DecisionRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listDecisions(filter).then((r) => { setRows(r); setError(null); }).catch((e: Error) => setError(e.message));
  }, [filter]);
  useEffect(load, [load]);

  async function review(id: string, outcome: "approve" | "reject") {
    const reason = window.prompt(`Reason to ${outcome}:`);
    if (!reason || reason.trim().length < 3) return;
    try { await reviewDecision(id, outcome, reason.trim()); load(); } catch (e) { setError((e as Error).message); }
  }

  return (
    <section>
      <div className="toolbar">
        <h2>Decisions & reviews</h2>
        <div className="segmented" role="group" aria-label="Filter">
          {FILTERS.map((f) => (
            <button key={f.label} className={f.q === filter ? "on" : ""} onClick={() => setFilter(f.q)}>{f.label}</button>
          ))}
        </div>
      </div>
      <ErrorNote error={error} />
      <div className="card scroll">
        <table>
          <thead>
            <tr><th>Time</th><th>Decision</th><th>Risk</th><th>Intent</th><th>Destination</th><th>Policies</th><th>Review</th></tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={7} className="muted">No decisions.</td></tr>}
            {rows.map((d) => (
              <tr key={d.decision_id}>
                <td className="nowrap">{new Date(d.created_at).toLocaleString()}</td>
                <td><DecisionBadge value={d.final_decision} /></td>
                <td>{d.risk_score.toFixed(2)}</td>
                <td>{d.intent_label}</td>
                <td>{d.destination_model} <span className="muted">({d.destination_jurisdiction})</span></td>
                <td className="small">{d.policy_ids.join(", ")}</td>
                <td>
                  {d.review_status === "pending" ? (
                    <span className="nowrap">
                      <button onClick={() => review(d.decision_id, "approve")}>Approve</button>{" "}
                      <button className="danger" onClick={() => review(d.decision_id, "reject")}>Reject</button>
                    </span>
                  ) : <span className="muted">{d.review_status.replaceAll("_", " ")}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
