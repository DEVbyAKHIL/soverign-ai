import { useEffect, useState } from "react";
import { listPolicies, type Policy } from "../api/client";
import { ErrorNote } from "../components/ui";

export default function Policies() {
  const [data, setData] = useState<{ policy_set_version: string; policies: Policy[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { listPolicies().then(setData).catch((e: Error) => setError(e.message)); }, []);

  return (
    <section>
      <h2>Policies</h2>
      <ErrorNote error={error} />
      {data && (
        <>
          <p className="muted">Policy set {data.policy_set_version} · {data.policies.length} policies · default deny</p>
          <div className="card scroll">
            <table>
              <thead><tr><th>Policy</th><th>Severity</th><th>Actions</th><th>Description</th></tr></thead>
              <tbody>
                {data.policies.map((p) => (
                  <tr key={`${p.policy_id}@${p.version}`}>
                    <td><strong>{p.name}</strong><div className="muted small">{p.policy_id}@{p.version}</div></td>
                    <td>{p.severity}</td>
                    <td className="small">{p.actions.join(", ")}</td>
                    <td className="small">{p.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
