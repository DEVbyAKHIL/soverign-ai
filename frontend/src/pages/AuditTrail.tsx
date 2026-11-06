import { useEffect, useState } from "react";
import { listAudit, verifyAudit, type AuditEvent } from "../api/client";
import { ErrorNote } from "../components/ui";

type Verify = Awaited<ReturnType<typeof verifyAudit>>;

function summary(e: AuditEvent): string {
  const d = e.event_data as Record<string, any>;
  if (e.event_type === "decision.created") return `${d.endpoint} · ${d.final_decision} · risk ${Number(d.risk_score).toFixed(2)}`;
  if (e.event_type === "model.response") return `${d.model} · ${d.status}${d.output_guard ? ` · guard ${d.output_guard.action}` : ""}`;
  if (d.outcome) return `review ${d.outcome}`;
  return "";
}

export default function AuditTrail() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [verify, setVerify] = useState<Verify | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { listAudit().then(setEvents).catch((e: Error) => setError(e.message)); }, []);

  return (
    <section>
      <div className="toolbar">
        <h2>Audit trail</h2>
        <button onClick={() => verifyAudit().then(setVerify).catch((e: Error) => setError(e.message))}>
          Verify hash chain
        </button>
      </div>
      <p className="muted">Append-only, hash-chained per tenant. No prompts or answers are stored — only hashes and verdicts.</p>
      <ErrorNote error={error} />
      {verify && (
        <p className={verify.valid ? "good" : "bad"}>
          {verify.valid ? `✓ Chain intact — ${verify.events_checked} events verified.`
            : `✕ Chain broken at event ${verify.first_invalid_seq}: ${verify.reason}`}
        </p>
      )}
      <div className="card scroll">
        <table>
          <thead><tr><th>#</th><th>Time</th><th>Event</th><th>Summary</th><th>Hash</th></tr></thead>
          <tbody>
            {events.length === 0 && <tr><td colSpan={5} className="muted">No events.</td></tr>}
            {events.map((e) => (
              <tr key={e.id}>
                <td>{e.seq}</td>
                <td className="nowrap">{new Date(e.created_at).toLocaleString()}</td>
                <td>{e.event_type}</td>
                <td className="small">{summary(e)}</td>
                <td className="mono small" title={e.event_hash}>{e.event_hash.slice(0, 12)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
