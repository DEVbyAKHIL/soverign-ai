import { useState } from "react";
import { getSeries } from "../../api/reporting";
import { LiveHeader, SeriesChart, ms, pivot } from "../../components/live";
import { usePolling } from "../../hooks/usePolling";

const LABELS: Record<string, string> = { avg_ms: "Average", p99_ms: "P99", max_ms: "Max" };

export default function LiveLatency() {
  const [minutes, setMinutes] = useState(60);
  const live = usePolling((s) => getSeries("latency", minutes, {}, s), [minutes]);
  const items = live.data?.items ?? [];
  const main = pivot(items.filter((p) => p.metric !== "max_ms"), (p) => p.metric);
  const max = pivot(items.filter((p) => p.metric === "max_ms"), (p) => p.metric);
  const failed = usePolling((s) => getSeries("policies", minutes, { metric: "failed_evaluations" }, s), [minutes]);
  const f = pivot(failed.data?.items ?? [], () => "failed");
  return (
    <section>
      <LiveHeader title="Latency and reliability" minutes={minutes} setMinutes={setMinutes}
        updatedAt={live.updatedAt} asOf={live.data?.as_of} error={live.error ?? failed.error} />
      <SeriesChart title="Gateway latency (5-min sliding windows)" rows={main.rows}
        keys={["avg_ms", "p99_ms"].filter((k) => main.keys.includes(k))} labelOf={(k) => LABELS[k] ?? k}
        yFormat={(v) => ms(v)} note="P99 = approximate 99th percentile (Spark percentile_approx) per window." />
      <div className="grid2">
        <SeriesChart title="Slowest request per window" rows={max.rows} keys={max.keys}
          labelOf={() => "Max"} colorOf={() => "var(--series-6)"} yFormat={(v) => ms(v)} />
        <SeriesChart title="Failed evaluations (5-min sliding)" rows={f.rows} keys={f.keys}
          labelOf={() => "Failed"} colorOf={() => "var(--status-serious)"} />
      </div>
    </section>
  );
}
