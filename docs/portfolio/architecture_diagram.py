"""Render docs/portfolio/architecture.png (Phase 1 architecture diagram).

    pip install matplotlib==3.10.7
    python docs/portfolio/architecture_diagram.py

Pure layout code: boxes, grouped lanes, numbered flow steps, annotation pills and a legend.
Coordinates are in a 120 x 74 unit canvas (1 unit = 0.25 inch).
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).with_name("architecture.png")
plt.rcParams["font.family"] = "Arial"

# Category colours: (border / text accent, fill). High contrast on white.
C = {
    "gateway": ("#1f4e9e", "#e3ecfb"),
    "security": ("#5b2a86", "#efe6f8"),
    "stream": ("#b35500", "#fdeedd"),
    "storage": ("#1d6b3a", "#e0f2e6"),
    "report": ("#0b6e78", "#dcf3f5"),
    "monitor": ("#4a4f57", "#eef0f3"),
}
INK, MUTED, RED = "#1b1f24", "#4a4f57", "#b3261e"
PILL = ("#7a5a00", "#fff3c4")

fig = plt.figure(figsize=(30, 18.5))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 120)
ax.set_ylim(-10, 64)
ax.axis("off")
fig.patch.set_facecolor("white")


def rect(x0, y0, x1, y1, cat, *, fill=True, lw=2.0, ls="-", alpha=1.0, z=1, edge=None):
    edge = edge or C[cat][0]
    ax.add_patch(
        FancyBboxPatch(
            (x0, y0), x1 - x0, y1 - y0,
            boxstyle="round,pad=0,rounding_size=0.8",
            facecolor=C[cat][1] if fill else "none", edgecolor=edge,
            linewidth=lw, linestyle=ls, alpha=alpha, zorder=z,
        )
    )  # fmt: skip


def box(x0, y0, x1, y1, cat, title, subs=(), edge=None, title_size=13.5):
    rect(x0, y0, x1, y1, cat, z=3, edge=edge)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    lines = [subs] if isinstance(subs, str) else list(subs)
    step = 1.35
    top = cy + (len(lines) * step) / 2
    ax.text(cx, top, title, ha="center", va="center", fontsize=title_size, fontweight="bold",
            color=edge or C[cat][0], zorder=4)  # fmt: skip
    for i, s in enumerate(lines):
        ax.text(cx, top - step * (i + 1) - 0.15, s, ha="center", va="center", fontsize=10.5,
                color=INK, zorder=4)  # fmt: skip


def group(x0, y0, x1, y1, cat, title, sub=None):
    rect(x0, y0, x1, y1, cat, lw=1.6, alpha=0.55, z=1)
    rect(x0, y0, x1, y1, cat, fill=False, lw=1.8, z=2)
    ax.text(x0 + 1.1, y1 - 1.4, title, ha="left", va="center", fontsize=15, fontweight="bold",
            color=C[cat][0], zorder=4)  # fmt: skip
    if sub:
        ax.text(x0 + 1.1, y1 - 3.0, sub, ha="left", va="center", fontsize=10.5, color=MUTED,
                zorder=4)  # fmt: skip


def arrow(p, q, color, style="-", label=None, lpos=None, lha="center", rad=0.0, lw=2.4):
    ax.add_patch(
        FancyArrowPatch(
            p, q, arrowstyle="-|>", mutation_scale=22, color=color, linewidth=lw,
            linestyle=style, connectionstyle=f"arc3,rad={rad}", zorder=5,
            shrinkA=0, shrinkB=0,
        )
    )  # fmt: skip
    if label:
        x, y = lpos or ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2 + 0.9)
        ax.text(x, y, label, ha=lha, va="center", fontsize=10.5, color=color, zorder=6,
                fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.9))  # fmt: skip


def line(points, color, style="-", lw=2.4):
    xs, ys = zip(*points, strict=True)
    ax.plot(xs, ys, color=color, linestyle=style, linewidth=lw, zorder=5, solid_capstyle="round")


def badge(x, y, n, color):
    ax.add_patch(Circle((x, y), 0.95, facecolor=color, edgecolor="white", linewidth=1.5, zorder=7))
    ax.text(x, y, str(n), ha="center", va="center", fontsize=11, fontweight="bold",
            color="white", zorder=8)  # fmt: skip


def pill(x, y, text, size=12):
    ax.text(x, y, text, ha="center", va="center", fontsize=size, fontweight="bold",
            fontstyle="italic", color=PILL[0], zorder=9,
            bbox=dict(boxstyle="round,pad=0.35,rounding_size=0.8", fc=PILL[1], ec=PILL[0], lw=1.4))  # fmt: skip


DASH = (0, (5, 3))
DOT = (0, (1, 2.5))

# ------------------------------------------------------------------------- title
ax.text(1.5, 62.0, "Sovereign AI Access Gateway: A Real-Time Streaming Data Backend",
        fontsize=24, fontweight="bold", color=INK, va="center")  # fmt: skip
ax.text(1.5, 59.6,
        "for AI Governance, Security, and Operational Analytics   ·   Phase 1 architecture   ·   "
        "all services run locally with Docker Compose",
        fontsize=13.5, color=MUTED, va="center")  # fmt: skip

# ------------------------------------------------------------------------- monitoring
rect(14, 53.4, 119.5, 56.9, "monitor", z=3)
ax.text(66.75, 55.15,
        "Monitoring & metrics  —  Prometheus /metrics  ·  health & readiness checks  ·  "
        "structured JSON logs  ·  alert rules",
        ha="center", va="center", fontsize=13, fontweight="bold", color=C["monitor"][0], zorder=4)  # fmt: skip
for x, top in ((28, 51.5), (50.5, 50.2), (67, 51.5), (91, 51.5), (112.5, 51.5)):
    line([(x, 53.4), (x, top)], C["monitor"][0], style=DOT, lw=2.0)

# ------------------------------------------------------------------------- client
box(1.5, 38.5, 11.5, 47.5, "gateway", "AI client", ("or AI agent", "", "users · services", "agents"),
    title_size=15)  # fmt: skip

# ------------------------------------------------------------------------- gateway (synchronous)
group(14, 18.8, 42, 51.5, "gateway", "Sovereign AI Access Gateway")
pill(35.0, 50.1, "Synchronous policy enforcement", size=12)
box(16, 40.5, 40, 46.2, "security", "Authentication & authorisation",
    "JWT · RBAC scopes · tenant isolation")  # fmt: skip
box(16, 33.5, 40, 39.2, "security", "PII & sensitive-data detection",
    "regex · NER · sensitivity classifier")  # fmt: skip
box(16, 26.5, 40, 32.2, "security", "Prompt-injection & intent-risk",
    "rules + ML classifier · direct & indirect")  # fmt: skip
box(16, 19.6, 40, 25.3, "security", "Policy-as-code engine",
    "versioned rules · deterministic · default deny")  # fmt: skip
for y0, y1 in ((40.5, 39.2), (33.5, 32.2), (26.5, 25.3)):
    arrow((28, y0), (28, y1), C["security"][0], lw=2.2)
badge(41.0, 43.35, 2, C["security"][0])
badge(41.0, 36.35, 3, C["security"][0])
badge(41.0, 29.35, 3, C["security"][0])
badge(41.0, 22.45, 4, C["security"][0])

arrow((11.5, 44.6), (16, 44.6), C["gateway"][0], label="AI request", lpos=(13.7, 46.0))
badge(13.75, 43.0, 1, C["gateway"][0])
arrow((16, 41.3), (11.5, 41.3), C["gateway"][0], label="response", lpos=(13.7, 39.9))

# ------------------------------------------------------------------------- storage (left)
group(14, 1, 42, 11.6, "storage", "PostgreSQL")
rect(15.0, 1.6, 41.0, 9.0, "storage", fill=False, lw=1.8, ls=DASH, z=2)
box(
    16,
    2.2,
    27.5,
    8.4,
    "storage",
    "Decision store",
    ("decisions · detections", "hash-chained audit"),
)
box(29.5, 2.2, 40, 8.4, "storage", "Transactional", ("outbox", "value-free event"))
arrow((28, 19.6), (28, 9.0), C["storage"][0], label="save decision + event",
      lpos=(27.0, 15.4), lha="right")  # fmt: skip
ax.text(27.0, 13.6, "in one ACID transaction", ha="right", va="center", fontsize=10.5,
        color=C["storage"][0], zorder=6)  # fmt: skip
badge(29.6, 14.5, 5, C["storage"][0])
pill(36.6, 16.4, "Transactional outbox")

# ------------------------------------------------------------------------- publisher / producer lane
box(45, 2.6, 56, 9.4, "stream", "Event publisher", ("polls outbox", "marks sent after ack"))
arrow((40, 5.3), (45, 5.9), C["stream"][0], style=DASH, label="read", lpos=(42.5, 7.0))
badge(42.5, 3.9, 6, C["stream"][0])
box(
    45, 44.2, 56, 50.2, "stream", "Event producer", ("synthetic telemetry", "continuous · backfill")
)
line([(50.5, 9.4), (50.5, 34.0)], C["stream"][0], style=DASH)
line([(50.5, 44.2), (50.5, 34.0)], C["stream"][0], style=DASH)
arrow((50.5, 34.0), (60.5, 34.0), C["stream"][0], style=DASH, label="publish",
      lpos=(55.6, 35.2))  # fmt: skip
badge(52.2, 27.0, 7, C["stream"][0])
pill(50.5, 21.5, "At-least-once\nKafka delivery", size=12)
pill(50.5, 40.0, "1M+ timestamped\ngovernance events", size=12)

# ------------------------------------------------------------------------- Kafka
group(59, 18.8, 75, 51.5, "stream", "Apache Kafka", "KRaft · partitioned by tenant")
pill(67, 46.9, "Dead-letter handling", size=12)
box(60.5, 39.0, 73.5, 44.6, "stream", "governance.events.dead-letter", "reason + offset, no payload",
    edge=RED, title_size=11.5)  # fmt: skip
box(60.5, 31.0, 73.5, 36.6, "stream", "governance.events.raw", "6 partitions · 7-day retention",
    title_size=11.5)  # fmt: skip
box(60.5, 22.0, 73.5, 27.6, "stream", "governance.events.valid", "6 partitions",
    title_size=11.5)  # fmt: skip
badge(59.6, 33.8, 8, C["stream"][0])

# ------------------------------------------------------------------------- Spark
group(77.5, 18.8, 104, 51.5, "stream", "Spark Structured Streaming", "micro-batches · 9 queries")
box(79.5, 31.0, 91, 36.6, "stream", "Validate & enrich", ("JSON Schema · UTC time",))
box(79.5, 22.0, 91, 27.6, "stream", "Watermark + dedup", ("5-min watermark · event_id",))
box(92.5, 22.0, 102.5, 45.0, "stream", "Windowed", (
    "aggregation", "", "1-min tumbling", "5-min sliding", "5 / 10-min windows", "",
    "11 metrics", "per tenant / agent", "/ model / jurisdiction",
))  # fmt: skip
arrow((73.5, 33.2), (79.5, 33.2), C["stream"][0], style=DASH, label="consume", lpos=(76.5, 34.4))
badge(76.5, 31.8, 9, C["stream"][0])
badge(85.25, 37.9, 10, C["stream"][0])
arrow((79.5, 35.6), (73.5, 41.2), RED, style=DASH, label="invalid", lpos=(77.2, 39.6))
badge(75.6, 37.3, 11, RED)
arrow((79.5, 31.6), (73.5, 26.4), C["stream"][0], style=DASH, label="valid", lpos=(77.0, 28.0))
arrow((73.5, 23.6), (79.5, 23.6), C["stream"][0], style=DASH)
badge(76.5, 22.6, 12, C["stream"][0])
arrow((91, 24.8), (92.5, 26.5), C["stream"][0], style=DASH)

box(79.5, 12.2, 91, 17.2, "storage", "Checkpoint storage", "offsets + window state")
line([(85.25, 17.2), (85.25, 18.8)], C["storage"][0], style=DOT, lw=2.4)
pill(85.25, 10.2, "Spark checkpointing", size=12)

# ------------------------------------------------------------------------- storage (right) + reporting
group(92, 1, 119.5, 11.6, "storage", "PostgreSQL")
box(93.5, 2.2, 118, 8.4, "storage", "Aggregate store", "windowed metrics · upsert on window + key")
arrow((100.5, 22.0), (100.5, 8.4), C["storage"][0], style=DASH, label="upsert", lpos=(100.5, 19.6))
badge(102.4, 16.6, 13, C["storage"][0])
pill(100.5, 13.6, "Idempotent aggregation")

group(105.5, 18.8, 119.5, 51.5, "report", "Reporting")
pill(112.5, 47.0, "Real-time reporting", size=12)
box(106.8, 22.0, 118.2, 28.6, "report", "Reporting API", ("read-only", "tenant from token"))
box(106.8, 33.5, 118.2, 41.0, "report", "React dashboard", ("live metrics", "5 s refresh"))
arrow((112.5, 8.4), (112.5, 22.0), C["report"][0], label="read latest", lpos=(115.7, 15.6))
badge(110.6, 15.6, 14, C["report"][0])
arrow((112.5, 28.6), (112.5, 33.5), C["report"][0])
badge(114.4, 31.0, 15, C["report"][0])

# ------------------------------------------------------------------------- legend
ax.add_patch(FancyBboxPatch((1.5, -9.2), 118, 7.4, boxstyle="round,pad=0,rounding_size=0.8",
                            facecolor="white", edgecolor="#9aa1ab", linewidth=1.4, zorder=1))  # fmt: skip
ax.text(2.8, -3.0, "Legend", fontsize=14, fontweight="bold", color=INK, va="center")
lx = 10.5
for label, style, color in (
    ("Synchronous request path", "-", C["gateway"][0]),
    ("Asynchronous event streaming", DASH, C["stream"][0]),
    ("Checkpoint state / monitoring", DOT, C["monitor"][0]),
):
    ax.add_patch(FancyArrowPatch((lx, -3.0), (lx + 6, -3.0), arrowstyle="-|>", mutation_scale=20,
                                 color=color, linewidth=2.4, linestyle=style))  # fmt: skip
    ax.text(lx + 7, -3.0, label, fontsize=12, va="center", color=INK)
    lx += 26
badge(lx + 0.5, -3.0, 1, C["gateway"][0])
ax.text(lx + 2.2, -3.0, "Flow step (1-15)", fontsize=12, va="center", color=INK)
pill(lx + 17.5, -3.0, "Design guarantee", size=12)
cx = 2.8
for label, cat in (
    ("Client & gateway", "gateway"), ("Security & policy controls", "security"),
    ("Kafka & streaming", "stream"), ("Storage", "storage"),
    ("Reporting & dashboard", "report"), ("Monitoring", "monitor"),
):  # fmt: skip
    ax.add_patch(FancyBboxPatch((cx, -7.6), 2.4, 1.6, boxstyle="round,pad=0,rounding_size=0.3",
                                facecolor=C[cat][1], edgecolor=C[cat][0], linewidth=1.8))  # fmt: skip
    ax.text(cx + 3.1, -6.8, label, fontsize=12, va="center", color=INK)
    cx += 16.6
ax.add_patch(FancyBboxPatch((cx, -7.6), 2.4, 1.6, boxstyle="round,pad=0,rounding_size=0.3",
                            facecolor=C["stream"][1], edgecolor=RED, linewidth=2.0))  # fmt: skip
ax.text(cx + 3.1, -6.8, "Dead-letter (invalid events)", fontsize=12, va="center", color=INK)

fig.savefig(OUT, dpi=150, facecolor="white")
print(f"wrote {OUT}")
