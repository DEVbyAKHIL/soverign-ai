"""Long-running agent risk score (stored on `agents.risk_score`)."""

RISK_EWMA_ALPHA = 0.2


def updated_risk(previous: float, anomaly_score: float) -> float:
    """Exponentially weighted moving average of anomaly scores: a single spike raises the risk,
    sustained anomalous behaviour keeps it high, and normal behaviour lets it decay."""
    return round((1 - RISK_EWMA_ALPHA) * previous + RISK_EWMA_ALPHA * anomaly_score, 4)
