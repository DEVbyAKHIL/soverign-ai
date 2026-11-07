import { useState } from "react";
import { getSeries } from "../../api/reporting";
import { LiveHeader, SeriesChart, pivot } from "../../components/live";
import { usePolling } from "../../hooks/usePolling";

const JURISDICTION: Record<string, { label: string; color: string }> = {
  eu: { label: "EU", color: "var(--series-1)" },
  non_eu: { label: "Non-EU", color: "var(--series-2)" },
  unknown: { label: "Unknown", color: "var(--series-6)" },
};
const LEVELS = ["public", "internal", "confidential", "restricted", "high_risk"];

export default function LiveSovereignty() {
  const [minutes, setMinutes] = useState(360);
  const live = usePolling((s) => getSeries("sovereignty", minutes, { metric: "events" }, s), [minutes]);
  const items = live.data?.items ?? [];
  const { rows, keys } = pivot(items, (p) => p.dimensions.destination_jurisdiction);
  const matrix = new Map<string, number>();
  for (const p of items) {
    const k = `${p.dimensions.destination_jurisdiction}|${p.dimensions.sensitivity_level}`;
    matrix.set(k, (matrix.get(k) ?? 0) + (p.value ?? 0));
  }
  const suppressed = items.filter((p) => p.suppressed).length;
  return (
    <section>
      <LiveHeader title="Sovereignty" minutes={minutes} setMinutes={setMinutes}
        updatedAt={live.updatedAt} asOf={live.data?.as_of} error={live.error}>
        {suppressed > 0 && <span>{suppressed} small cell(s) withheld (fewer than 5 events).</span>}
      </LiveHeader>
      <SeriesChart title="Requests by destination jurisdiction (10-min tumbling)" rows={rows}
        keys={Object.keys(JURISDICTION).filter((k) => keys.includes(k))} kind="bar" stacked
        colorOf={(k) => JURISDICTION[k]?.color ?? "var(--series-6)"} labelOf={(k) => JURISDICTION[k]?.label ?? k} />
      <div className="card">
        <h3>Data sensitivity × jurisdiction (requests in range)</h3>
        <table>
          <thead><tr><th>Sensitivity</th>{Object.values(JURISDICTION).map((j) => <th key={j.label}>{j.label}</th>)}</tr></thead>
          <tbody>
            {LEVELS.map((lvl) => (
              <tr key={lvl}>
                <td>{lvl}</td>
                {Object.keys(JURISDICTION).map((j) => {
                  const v = matrix.get(`${j}|${lvl}`) ?? 0;
                  const risky = j !== "eu" && ["confidential", "restricted", "high_risk"].includes(lvl) && v > 0;
                  return <td key={j} className={risky ? "bad" : undefined}>{v.toLocaleString()}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">Highlighted: sensitive data with a non-EU or unknown destination (decided per policy: usually denied).</p>
      </div>
    </section>
  );
}
