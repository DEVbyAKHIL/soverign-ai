import { useState } from "react";
import { getSeries } from "../../api/reporting";
import { LiveHeader, SeriesChart, pivot } from "../../components/live";
import { usePolling } from "../../hooks/usePolling";

export default function LivePii() {
  const [minutes, setMinutes] = useState(360);
  const live = usePolling((s) => getSeries("pii", minutes, { metric: "pii_entities" }, s), [minutes]);
  const items = live.data?.items ?? [];
  const { rows, keys } = pivot(items, (p) => p.dimensions.destination_model);
  const suppressed = items.filter((p) => p.suppressed).length;
  const totals = Object.fromEntries(keys.map((k) => [k, rows.reduce((a, r) => a + ((r[k] as number) ?? 0), 0)]));
  return (
    <section>
      <LiveHeader title="PII detections" minutes={minutes} setMinutes={setMinutes}
        updatedAt={live.updatedAt} asOf={live.data?.as_of} error={live.error}>
        {suppressed > 0 && <span>{suppressed} small cell(s) withheld (fewer than 5 events).</span>}
      </LiveHeader>
      <SeriesChart title="Sensitive entities detected per destination model (5-min tumbling)" rows={rows}
        keys={keys} kind="bar" stacked
        note="Counts only: entity values never leave the gateway; the stream carries counts per event." />
      <div className="card">
        <h3>Totals in range</h3>
        <table>
          <thead><tr><th>Destination model</th><th>PII entities</th></tr></thead>
          <tbody>{Object.entries(totals).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
            <tr key={k}><td>{k}</td><td>{v.toLocaleString()}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}
