"""Evaluate the injection detector on a real public dataset (deepset/prompt-injections).

    python -m ml.evaluation.evaluate_real_injection [--model PATH] [--out PATH]

Dataset: https://huggingface.co/datasets/deepset/prompt-injections (Apache-2.0), 116-row test
split, English and German, binary labels (1 = injection). Only the test split is used here; the
train split is what `train_injection_classifier --extra-csv` may add. Prompts are scored as
"direct" input (the caller's own words), as the dataset intends.

Reported: binary precision / recall / F1 / FPR where "attack" means an attack label, plus the
same with "suspicious" (alerted, not blocked) counted as flagged.
"""

import argparse
import csv
import json
from pathlib import Path

from sklearn.metrics import precision_recall_fscore_support, roc_auc_score

from detection.injection.classifier import InjectionClassifier
from detection.injection.detector import ATTACK_LABELS, InjectionDetector

DATA = Path(__file__).resolve().parents[1] / "data" / "external"
TEST_CSV = DATA / "deepset_prompt_injections_test.csv"
OUT = Path(__file__).resolve().parent / "results" / "injection_real_eval.json"


def load(path: Path) -> list[tuple[str, int]]:
    with path.open(encoding="utf-8") as f:
        return [(r["text"], int(r["label"])) for r in csv.DictReader(f)]


def _binary(y: list[int], pred: list[int]) -> dict:
    p, r, f, _ = precision_recall_fscore_support(y, pred, average="binary", zero_division=0)
    fp = sum(1 for a, b in zip(y, pred, strict=True) if a == 0 and b == 1)
    negatives = y.count(0)
    return {
        "precision": round(float(p), 4),
        "recall": round(float(r), 4),
        "f1": round(float(f), 4),
        "false_positive_rate": round(fp / negatives, 4) if negatives else 0.0,
    }


def evaluate(model: Path | None = None) -> dict:
    rows = load(TEST_CSV)
    det = InjectionDetector()
    if model is not None:
        det.classifier = InjectionClassifier(model)
    results = [det.analyze(text, "direct", 0) for text, _ in rows]
    y = [label for _, label in rows]
    blocked = [int(r.label in ATTACK_LABELS) for r in results]
    flagged = [int(r.label in ATTACK_LABELS or r.label == "suspicious") for r in results]
    return {
        "dataset": "deepset/prompt-injections test split (Apache-2.0)",
        "rows": len(rows),
        "attacks": sum(y),
        "classifier_version": det.model_versions.get("injection_classifier"),
        "attack_label": _binary(y, blocked),
        "attack_or_suspicious": _binary(y, flagged),
        "roc_auc": round(float(roc_auc_score(y, [r.risk_score for r in results])), 4),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    metrics = evaluate(args.model)
    args.out.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))
