"""Train the agent-behaviour Isolation Forest on normal windows and export it as JSON.

    python -m ml.training.train_anomaly_detector [--out PATH]

Trained on *normal* behaviour only (unsupervised): 120 simulated agents x 40 windows. The score is
calibrated so the 99.5th percentile of training-normal windows maps to the anomaly threshold (0.7),
i.e. ~0.5% expected false-positive rate from the forest alone, before the statistical rules.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest

from agents.features import FEATURE_NAMES
from ml.data.agents import dataset

VERSION = "agent-iforest-1.0.0"
# Normal-window quantile mapped to the anomaly threshold. 0.995 chosen on validation (seed 55):
# q0.99 added +1.2 pt FPR for ~+1.6 pt subtle-anomaly recall; q0.995 halves that FPR cost.
THRESHOLD_QUANTILE = 0.995
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "agents" / "models" / "isolation_forest_v1.json"


def export(forest: IsolationForest) -> list[dict]:
    trees = []
    for est, feats in zip(forest.estimators_, forest.estimators_features_, strict=True):
        t = est.tree_
        trees.append(
            {
                "l": t.children_left.tolist(),
                "r": t.children_right.tolist(),
                "f": [int(v) for v in t.feature],
                "t": [float(v) for v in t.threshold],
                "n": [int(v) for v in t.n_node_samples],
                "fmap": [int(v) for v in feats],
            }
        )
    return trees


def train(out: Path) -> dict:
    samples = [s for s in dataset(120, 40, 0, seed=10) if s.label == "normal"]
    x = np.array([s.features for s in samples], dtype=np.float64)
    forest = IsolationForest(n_estimators=100, max_samples=256, random_state=0)
    forest.fit(x)
    raw = -forest.score_samples(x)
    calibration = {
        "median": float(np.median(raw)),
        "threshold": float(np.quantile(raw, THRESHOLD_QUANTILE)),
        "threshold_quantile": THRESHOLD_QUANTILE,
    }
    model = {
        "format": "isolation-forest-v1",
        "version": VERSION,
        "features": list(FEATURE_NAMES),
        "max_samples": int(forest.max_samples_),
        "calibration": calibration,
        "trees": export(forest),
        "metrics": {"train_windows": len(samples), "train_agents": 120},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    return {"train_windows": len(samples), "calibration": calibration, "bytes": out.stat().st_size}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    print(json.dumps(train(parser.parse_args().out), indent=2))
