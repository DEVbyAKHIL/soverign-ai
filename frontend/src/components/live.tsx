import { useState, type ReactNode } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { MetricPoint } from "../api/reporting";
import { ErrorNote } from "./ui";

export const RANGES = [
  { minutes: 15, label: "15 min" },
  { minutes: 60, label: "1 h" },
  { minutes: 360, label: "6 h" },
  { minutes: 1440, label: "24 h" },
];

export const seriesColor = (i: number) => `var(--series-${(i % 6) + 1})`;

// Event-level decisions (governance event schema). Status colours, always with a text label.
export const EVENT_DECISIONS: Record<string, { label: string; color: string }> = {
  allow: { label: "Allowed", color: "var(--status-good)" },
  redact: { label: "Redacted", color: "var(--status-warning)" },
  route: { label: "Routed", color: "var(--series-1)" },
  require_review: { label: "Review", color: "var(--status-serious)" },
  deny: { label: "Denied", color: "var(--status-critical)" },
  quarantine: { label: "Quarantined", color: "#8f1d1d" },
};

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

export type Row = { t: string; ts: string } & Record<string, number | string | null>;

/** Long-format points -> one row per window, one column per key (dimension or metric). */
export function pivot(items: MetricPoint[], keyOf: (p: MetricPoint) => string): { rows: Row[]; keys: string[] } {
  const byWindow = new Map<string, Row>();
  const keys = new Set<string>();
  for (const p of items) {
    if (p.value === null) continue; // suppressed small cell
    const k = keyOf(p);
    keys.add(k);
    // Labelled by window start: a sliding window that is still open ends in the future.
    const row = byWindow.get(p.window_start) ?? ({ t: hhmm(p.window_start), ts: p.window_start } as Row);
    row[k] = ((row[k] as number) ?? 0) + p.value;
    byWindow.set(p.window_start, row);
  }
  const rows = [...byWindow.values()].sort((a, b) => a.ts.localeCompare(b.ts));
  return { rows, keys: [...keys] };
}

export function LiveHeader({
  title, minutes, setMinutes, updatedAt, asOf, error, children,
}: {
  title: string; minutes?: number; setMinutes?: (m: number) => void; updatedAt: Date | null;
  asOf?: string | null; error: string | null; children?: ReactNode;
}) {
  const ageS = asOf ? (Date.now() - new Date(asOf).getTime()) / 1000 : null;
  const stale = ageS !== null && ageS > 120;
  return (
    <>
      <div className="toolbar">
        <h2>{title}</h2>
        {setMinutes && (
          <div className="segmented" role="group" aria-label="Time range">
            {RANGES.map((r) => (
              <button key={r.minutes} className={r.minutes === minutes ? "on" : ""} onClick={() => setMinutes(r.minutes)}>
                {r.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <p className="muted small live-note">
        <span>
          <span className="dot" style={{ background: stale ? "var(--status-serious)" : "var(--status-good)" }} />
          {updatedAt ? `Refreshed ${updatedAt.toLocaleTimeString()} · every 5 s` : "Loading…"}
        </span>
        {asOf && <span>Processor wrote this data at {new Date(asOf).toLocaleTimeString()}{stale ? " (stale)" : ""}</span>}
        <span>Event-time windows from the streaming pipeline; includes synthetic producer traffic.</span>
        {children}
      </p>
      <ErrorNote error={error} />
    </>
  );
}

export function SeriesChart({
  title, rows, keys, kind = "line", stacked = false, colorOf, labelOf, note, yFormat,
}: {
  title: string; rows: Row[]; keys: string[]; kind?: "line" | "bar"; stacked?: boolean;
  colorOf?: (k: string, i: number) => string; labelOf?: (k: string) => string; note?: string;
  yFormat?: (v: number) => string;
}) {
  const [table, setTable] = useState(false);
  const color = (k: string, i: number) => (colorOf ? colorOf(k, i) : seriesColor(i));
  const label = (k: string) => (labelOf ? labelOf(k) : k);
  const axis = { tick: { fill: "var(--muted)", fontSize: 12 }, stroke: "var(--border)" };
  const fmt = (v: unknown) => (typeof v === "number" ? (yFormat ? yFormat(v) : v.toLocaleString()) : String(v));
  return (
    <div className="card">
      <div className="toolbar">
        <h3>{title}</h3>
        <button className="link" onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button>
      </div>
      {note && <p className="muted small">{note}</p>}
      {rows.length === 0 ? <p className="muted">No data in this range yet.</p> : table ? (
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead><tr><th>Window start</th>{keys.map((k) => <th key={k}>{label(k)}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.ts}><td>{r.t}</td>{keys.map((k) => <td key={k}>{r[k] == null ? "–" : fmt(r[k])}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          {kind === "bar" ? (
            <BarChart data={rows}>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis dataKey="t" {...axis} minTickGap={24} />
              <YAxis {...axis} tickFormatter={(v: number) => fmt(v)} width={56} />
              <Tooltip formatter={(v) => fmt(v)} />
              <Legend />
              {keys.map((k, i) => (
                <Bar key={k} dataKey={k} name={label(k)} fill={color(k, i)} stackId={stacked ? "s" : undefined}
                  isAnimationActive={false} maxBarSize={28} />
              ))}
            </BarChart>
          ) : (
            <LineChart data={rows}>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis dataKey="t" {...axis} minTickGap={24} />
              <YAxis {...axis} tickFormatter={(v: number) => fmt(v)} width={56} />
              <Tooltip formatter={(v) => fmt(v)} />
              <Legend />
              {keys.map((k, i) => (
                <Line key={k} dataKey={k} name={label(k)} stroke={color(k, i)} strokeWidth={2} dot={false}
                  isAnimationActive={false} connectNulls />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      )}
    </div>
  );
}

export const pct = (v: number | null | undefined) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
export const ms = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v).toLocaleString()} ms`);
export const shortId = (id: string) => id.slice(0, 8);
