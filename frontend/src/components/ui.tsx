import type { ReactNode } from "react";

// Decision outcomes are states, so they use the fixed status palette, always with a text label.
export const DECISION_META: Record<string, { label: string; color: string; icon: string }> = {
  allow: { label: "Allowed", color: "var(--status-good)", icon: "✓" },
  allow_with_modification: { label: "Modified", color: "var(--status-warning)", icon: "✎" },
  review: { label: "Human review", color: "var(--status-serious)", icon: "⏸" },
  deny: { label: "Denied", color: "var(--status-critical)", icon: "✕" },
};
export const DECISION_ORDER = ["allow", "allow_with_modification", "review", "deny"] as const;

export function DecisionBadge({ value }: { value: string }) {
  const m = DECISION_META[value] ?? { label: value, color: "var(--muted)", icon: "•" };
  return (
    <span className="badge">
      <i style={{ background: m.color }} aria-hidden>{m.icon}</i>
      {m.label}
    </span>
  );
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="card stat">
      <div className="muted">{label}</div>
      <div className="stat-value">{value}</div>
      {note && <div className="muted small">{note}</div>}
    </div>
  );
}

// Ranked single-series bars: one hue, value labels in ink, hover title per row.
export function BarList({ title, data, empty }: { title: string; data: Record<string, number>; empty: string }) {
  const rows = Object.entries(data).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <div className="card">
      <h3>{title}</h3>
      {rows.length === 0 ? <p className="muted">{empty}</p> : (
        <ul className="barlist">
          {rows.map(([k, n]) => (
            <li key={k} title={`${k}: ${n}`}>
              <span className="barlist-label">{k}</span>
              <span className="barlist-track"><span style={{ width: `${(n / max) * 100}%` }} /></span>
              <span className="barlist-value">{n}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <p className="bad">⚠ {error}</p> : null;
}
