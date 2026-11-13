import { useState } from "react";
import { getSeries } from "../../api/reporting";
import { LiveHeader, SeriesChart, pivot, shortId } from "../../components/live";
import { usePolling } from "../../hooks/usePolling";

const TOP = 5;

export default function LiveInjections() {
  const [minutes, setMinutes] = useState(60);
  const live = usePolling((s) => getSeries("injections", minutes, { metric: "attempts" }, s), [minutes]);
  const items = live.data?.items ?? [];
  // Top agents by attempts in range get their own line; the rest are summed ("other agents").
  const totals = new Map<string, number>();
  for (const p of items) totals.set(p.dimensions.agent_id, (totals.get(p.dimensions.agent_id) ?? 0) + (p.value ?? 0));
  const top = new Set([...totals.entries()].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, TOP).map(([k]) => k));
  const { rows, keys } = pivot(items, (p) => (top.has(p.dimensions.agent_id) ? p.dimensions.agent_id : "other"));
  const ordered = [...keys.filter((k) => k !== "other"), ...keys.filter((k) => k === "other")];
  return (
    <section>
      <LiveHeader title="Injection attempts" minutes={minutes} setMinutes={setMinutes}
        updatedAt={live.updatedAt} asOf={live.data?.as_of} error={live.error} />
      <SeriesChart title="Attempts per agent (5-min sliding, 1-min slide)" rows={rows} keys={ordered}
        labelOf={(k) => (k === "other" ? "Other agents" : `Agent ${shortId(k)}`)}
        colorOf={(k, i) => (k === "other" ? "var(--series-6)" : `var(--series-${(i % 5) + 1})`)}
        note="An attempt = injection risk score ≥ 0.6. Sliding windows overlap, so an attempt appears in up to 5 windows." />
    </section>
  );
}
