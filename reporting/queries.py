"""Tenant-scoped, parameterised reads of the aggregate tables (SQLAlchemy Core, no string SQL).

Every query takes tenant_id from the caller's token and binds it; nothing in a query string comes
from the request. Pagination is keyset-based on the table's primary-key order.
"""

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    MetaData,
    Table,
    Text,
    and_,
    desc,
    func,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.ext.asyncio import AsyncConnection

from reporting.schemas import AgentRisk, MetricPoint

NIL_AGENT = uuid.UUID(int=0)
metadata = MetaData()


def _table(name: str, *dims: tuple[str, object]) -> Table:
    return Table(
        name,
        metadata,
        Column("window_start", TIMESTAMP(timezone=True)),
        Column("window_end", TIMESTAMP(timezone=True)),
        Column("window_type", Text),
        Column("tenant_id", UUID(as_uuid=True)),
        *[Column(n, t) for n, t in dims],
        Column("metric", Text),
        Column("value", Float),
        Column("event_count", BigInteger),
        Column("processed_at", TIMESTAMP(timezone=True)),
    )


@dataclass(frozen=True)
class Source:
    table: Table
    dims: tuple[str, ...]
    suppress: bool = False

    def order(self):
        t = self.table
        return [t.c.window_start, t.c.window_end, *[t.c[d] for d in self.dims], t.c.metric]


AGENT_UUID = UUID(as_uuid=True)
SOURCES: dict[str, Source] = {
    "requests": Source(
        _table("aggregated_request_metrics", ("agent_id", AGENT_UUID)), ("agent_id",)
    ),
    "policies": Source(_table("aggregated_policy_metrics", ("decision", Text)), ("decision",)),
    "latency": Source(_table("aggregated_latency_metrics"), ()),
    "pii": Source(
        _table("aggregated_pii_metrics", ("destination_model", Text)),
        ("destination_model",),
        suppress=True,
    ),
    "injections": Source(
        _table("aggregated_injection_metrics", ("agent_id", AGENT_UUID)), ("agent_id",)
    ),
    "agents": Source(
        _table("aggregated_agent_risk_metrics", ("agent_id", AGENT_UUID)), ("agent_id",)
    ),
    "sovereignty": Source(
        _table(
            "aggregated_sovereignty_metrics",
            ("destination_jurisdiction", Text),
            ("sensitivity_level", Text),
        ),
        ("destination_jurisdiction", "sensitivity_level"),
        suppress=True,
    ),
}
progress = Table(
    "pipeline_progress",
    metadata,
    Column("query_name", Text),
    Column("batch_id", BigInteger),
    Column("processed_at", TIMESTAMP(timezone=True)),
)


class CursorError(ValueError):
    pass


def encode_cursor(values: list) -> str:
    raw = json.dumps([v.isoformat() if isinstance(v, datetime) else str(v) for v in values])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, src: Source) -> list:
    """Typed key values; any malformed cursor is a client error, never an SQL error."""
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(raw, list) or len(raw) != 3 + len(src.dims):
            raise CursorError("bad cursor")
        out: list = [datetime.fromisoformat(raw[0]), datetime.fromisoformat(raw[1])]
        for name, value in zip(src.dims, raw[2:-1], strict=True):
            out.append(uuid.UUID(value) if name == "agent_id" else str(value))
        out.append(str(raw[-1]))
        return out
    except (ValueError, TypeError) as exc:
        raise CursorError("bad cursor") from exc


@dataclass(frozen=True)
class Page:
    items: list[MetricPoint]
    next_cursor: str | None
    as_of: datetime | None


async def series(
    conn: AsyncConnection,
    kind: str,
    *,
    tenant_id: uuid.UUID,
    start: datetime,
    end: datetime,
    limit: int,
    cursor: str | None = None,
    filters: dict[str, object] | None = None,
    suppress_below: int = 5,
) -> Page:
    src = SOURCES[kind]
    t = src.table
    cond = [t.c.tenant_id == tenant_id, t.c.window_end > start, t.c.window_start < end]
    for name, value in (filters or {}).items():
        if value is not None:
            cond.append(t.c[name] == value)
    if cursor:
        cond.append(tuple_(*src.order()) > tuple_(*decode_cursor(cursor, src)))
    rows = (
        await conn.execute(select(t).where(and_(*cond)).order_by(*src.order()).limit(limit + 1))
    ).all()
    more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for r in rows:
        hide = src.suppress and r.event_count < suppress_below
        items.append(
            MetricPoint(
                window_start=r.window_start,
                window_end=r.window_end,
                window_type=r.window_type,
                dimensions={d: str(getattr(r, d)) for d in src.dims},
                metric=r.metric,
                value=None if hide else r.value,
                event_count=None if hide else r.event_count,
                suppressed=hide,
            )
        )
    last = rows[-1] if rows else None
    next_cursor = (
        encode_cursor([getattr(last, c.name) for c in src.order()]) if more and last else None
    )
    as_of = max((r.processed_at for r in rows), default=None)
    return Page(items, next_cursor, as_of)


RISK_METRICS = ("requests", "deny_rate", "injection_rate", "pii_rate", "risky_destination_share")


def _risk(agent_id, ws, we, metrics: dict[str, float]) -> AgentRisk:
    return AgentRisk(
        agent_id=agent_id,
        window_start=ws,
        window_end=we,
        anomaly_score=metrics.get("anomaly_score", 0.0),
        requests=int(metrics.get("requests", 0)),
        **{m: metrics.get(m) for m in RISK_METRICS[1:]},
    )


async def _pivot(conn: AsyncConnection, query) -> list[AgentRisk]:
    """Long-format risk rows (one per metric) -> one AgentRisk per agent window."""
    grouped: dict[tuple, dict[str, float]] = {}
    for r in (await conn.execute(query)).all():
        grouped.setdefault((r.agent_id, r.window_start, r.window_end), {})[r.metric] = r.value
    return [_risk(a, ws, we, m) for (a, ws, we), m in grouped.items()]


async def latest_agent_risk(
    conn: AsyncConnection, *, tenant_id: uuid.UUID, start: datetime, end: datetime, limit: int
) -> list[AgentRisk]:
    """Each agent's most recent 10-minute window in range, highest anomaly score first."""
    t = SOURCES["agents"].table
    latest = (
        select(t.c.agent_id, func.max(t.c.window_end).label("we"))
        .where(t.c.tenant_id == tenant_id, t.c.window_end > start, t.c.window_start < end)
        .group_by(t.c.agent_id)
        .subquery()
    )
    q = (
        select(t.c.agent_id, t.c.window_start, t.c.window_end, t.c.metric, t.c.value)
        .join(latest, and_(t.c.agent_id == latest.c.agent_id, t.c.window_end == latest.c.we))
        .where(t.c.tenant_id == tenant_id)
    )
    risks = await _pivot(conn, q)
    risks.sort(key=lambda r: (-r.anomaly_score, str(r.agent_id)))
    return risks[:limit]


async def agent_history(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[AgentRisk] | None:
    """None when the agent has no data for this tenant at all (-> 404, no existence leak)."""
    t = SOURCES["agents"].table
    known = await conn.scalar(
        select(func.count()).where(t.c.tenant_id == tenant_id, t.c.agent_id == agent_id).limit(1)
    )
    if not known:
        return None
    q = select(t.c.agent_id, t.c.window_start, t.c.window_end, t.c.metric, t.c.value).where(
        t.c.tenant_id == tenant_id,
        t.c.agent_id == agent_id,
        t.c.window_end > start,
        t.c.window_start < end,
    )
    return sorted(await _pivot(conn, q), key=lambda r: r.window_start)


async def overview(conn: AsyncConnection, tenant_id: uuid.UUID) -> dict:
    """KPIs from each table's most recent windows for this tenant (works for live and replayed
    data alike: 'latest' is relative to the data, not to the wall clock)."""
    req = SOURCES["requests"].table
    tenant_rows = and_(
        req.c.tenant_id == tenant_id, req.c.agent_id == NIL_AGENT, req.c.metric == "requests"
    )
    last_end = await conn.scalar(select(func.max(req.c.window_end)).where(tenant_rows))
    as_of = await conn.scalar(
        select(func.max(req.c.processed_at)).where(req.c.tenant_id == tenant_id)
    )
    out: dict = {
        "as_of": as_of,
        "latest_window_end": last_end,
        "requests_last_5m": 0,
        "requests_per_minute": 0.0,
        "decisions_last_5m": {},
        "pipeline_last_batch_at": await conn.scalar(select(func.max(progress.c.processed_at))),
    }
    if last_end is not None:
        recent = await conn.execute(
            select(req.c.window_start, req.c.value).where(
                tenant_rows, req.c.window_end > last_end - _minutes(5)
            )
        )
        values = [r.value for r in recent.all()]
        out["requests_last_5m"] = int(sum(values))
        out["requests_per_minute"] = round(sum(values) / max(1, len(values)), 2)
        pol = SOURCES["policies"].table
        decisions = await conn.execute(
            select(pol.c.decision, func.sum(pol.c.value))
            .where(
                pol.c.tenant_id == tenant_id,
                pol.c.metric == "count",
                pol.c.window_type == "tumbling",
                pol.c.window_end > last_end - _minutes(5),
                pol.c.window_end <= last_end,
            )
            .group_by(pol.c.decision)
        )
        out["decisions_last_5m"] = {d: int(v) for d, v in decisions.all()}
    out.update(
        await _latest_values(conn, "policies", tenant_id, ("deny_rate", "failed_evaluations"))
    )
    out.update(await _latest_values(conn, "latency", tenant_id, ("avg_ms", "p99_ms")))
    out.update(await _latest_sum(conn, "pii", tenant_id, "pii_entities"))
    out.update(await _latest_sum(conn, "injections", tenant_id, "attempts"))
    out["avg_latency_ms"] = out.pop("avg_ms", None)
    out["p99_latency_ms"] = out.pop("p99_ms", None)
    out["failed_evaluations"] = _int(out.get("failed_evaluations"))
    out["pii_entities_last_window"] = _int(out.pop("pii_entities", None))
    out["injection_attempts"] = _int(out.pop("attempts", None))
    return out


def _minutes(n: int):
    from datetime import timedelta

    return timedelta(minutes=n)


def _int(v) -> int | None:
    return None if v is None else int(v)


async def _latest_window_end(conn, t, tenant_id, metric):
    return await conn.scalar(
        select(func.max(t.c.window_end)).where(t.c.tenant_id == tenant_id, t.c.metric == metric)
    )


async def _latest_values(conn, kind, tenant_id, metrics) -> dict:
    t = SOURCES[kind].table
    out: dict = {}
    for m in metrics:
        end = await _latest_window_end(conn, t, tenant_id, m)
        if end is None:
            out[m] = None
            continue
        out[m] = await conn.scalar(
            select(t.c.value)
            .where(t.c.tenant_id == tenant_id, t.c.metric == m, t.c.window_end == end)
            .order_by(desc(t.c.processed_at))
            .limit(1)
        )
    return out


async def _latest_sum(conn, kind, tenant_id, metric) -> dict:
    t = SOURCES[kind].table
    end = await _latest_window_end(conn, t, tenant_id, metric)
    if end is None:
        return {metric: None}
    total = await conn.scalar(
        select(func.sum(t.c.value)).where(
            t.c.tenant_id == tenant_id, t.c.metric == metric, t.c.window_end == end
        )
    )
    return {metric: total}
