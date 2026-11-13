import { useState } from "react";
import { getSeries } from "../../api/reporting";
import { EVENT_DECISIONS, LiveHeader, SeriesChart, pct, pivot } from "../../components/live";
import { usePolling } from "../../hooks/usePolling";

export default function LivePolicies() {
  const [minutes, setMinutes] = useState(60);
  const live = usePolling((s) => getSeries("policies", minutes, {}, s), [minutes]);
  const items = live.data?.items ?? [];
  const counts = pivot(items.filter((p) => p.metric === "count"), (p) => p.dimensions.decision);
  const order = Object.keys(EVENT_DECISIONS).filter((k) => counts.keys.includes(k));
  const rates = pivot(items.filter((p) => p.metric === "deny_rate"), () => "deny_rate");
  const failed = pivot(items.filter((p) => p.metric === "failed_evaluations"), () => "failed");
  return (
    <section>
      <LiveHeader title="Policy decisions" minutes={minutes} setMinutes={setMinutes}
        updatedAt={live.updatedAt} asOf={live.data?.as_of} error={live.error} />
      <SeriesChart title="Decisions by type per minute (1-min tumbling)" rows={counts.rows} keys={order}
        kind="bar" stacked colorOf={(k) => EVENT_DECISIONS[k]?.color ?? "var(--series-6)"}
        labelOf={(k) => EVENT_DECISIONS[k]?.label ?? k} />
      <div className="grid2">
        <SeriesChart title="Deny rate (5-min sliding, 1-min slide)" rows={rates.rows} keys={rates.keys}
          labelOf={() => "Deny + quarantine share"} colorOf={() => "var(--status-critical)"} yFormat={(v) => pct(v)} />
        <SeriesChart title="Failed policy evaluations (5-min sliding)" rows={failed.rows} keys={failed.keys}
          labelOf={() => "Failed"} colorOf={() => "var(--status-serious)"} />
      </div>
    </section>
  );
}
