import { useEffect, useState } from "react";
import { getHealth, type Health } from "../api/client";

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <section>
      <h2>Overview</h2>
      <div className="card">
        <strong>Gateway status: </strong>
        {error ? <span className="bad">unreachable ({error})</span>
          : health ? <span className="good">{health.status} · v{health.version}</span>
          : "checking…"}
      </div>
      <p className="muted">Risk trends, policy violations and PII trends arrive in Phase 7.</p>
    </section>
  );
}
