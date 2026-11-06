"""Train the prompt-injection / intent classifier and export it as JSON.

    python -m ml.training.train_injection_classifier [--out PATH] [--extra-csv PATH]

Model: character n-grams (char_wb, 2-5) TF-IDF + multinomial logistic regression over
normalised text (same normalisation as the rule layer). Character n-grams are robust to
spacing/casing tricks and work across languages.

`--extra-csv` adds rows from a local CSV with columns `text,label` (e.g. a public prompt-injection
dataset you have downloaded and reviewed the licence of). Labels must be one of the model classes,
or 0/1 for a binary dataset (1 is mapped to `prompt_injection`, 0 to `benign`). The shipped model
adds the deepset/prompt-injections train split (ml/data/external, Apache-2.0).
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
    roc_auc_score,
)

from detection.injection.rules import normalise
from ml.data.injection import injection_corpus

VERSION = "injection-charlr-1.1.0"
BINARY = {"0": "benign", "1": "prompt_injection"}
CLASSES = sorted(
    [
        "benign",
        "prompt_injection",
        "jailbreak_attempt",
        "data_exfiltration_attempt",
        "system_prompt_extraction_attempt",
    ]
)
DEFAULT_OUT = (
    Path(__file__).resolve().parents[2]
    / "detection"
    / "injection"
    / "models"
    / "injection_charlr_v1.json"
)


def _load_extra(path: Path) -> list[tuple[str, str, str]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            label = BINARY.get(r["label"], r["label"])
            if label not in CLASSES:
                raise ValueError(f"unknown label {r['label']!r}")
            rows.append((r["text"], label, "direct"))
    return rows


def train(out: Path, extra_csv: Path | None = None) -> dict:
    train_rows = injection_corpus(300, seed=4, split="train")
    if extra_csv:
        train_rows += _load_extra(extra_csv)
    test_rows = injection_corpus(80, seed=77, split="heldout")

    x_train = [normalise(t) for t, _, _ in train_rows]
    y_train = [label for _, label, _ in train_rows]
    x_test = [normalise(t) for t, _, _ in test_rows]
    y_test = [label for _, label, _ in test_rows]

    vec = TfidfVectorizer(
        analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, min_df=3, max_features=40000
    )
    xtr = vec.fit_transform(x_train)
    clf = LogisticRegression(max_iter=3000, C=8.0, class_weight="balanced", random_state=0)
    clf.fit(xtr, y_train)

    xte = vec.transform(x_test)
    proba = clf.predict_proba(xte)
    pred = clf.predict(xte)
    labels = list(clf.classes_)
    benign_idx = labels.index("benign")
    y_bin = np.array([0 if y == "benign" else 1 for y in y_test])
    attack_score = 1 - proba[:, benign_idx]
    pred_bin = (attack_score >= 0.5).astype(int)
    p, r, f, _ = precision_recall_fscore_support(y_bin, pred_bin, average="binary")
    fp = int(((pred_bin == 1) & (y_bin == 0)).sum())
    tn = int(((pred_bin == 0) & (y_bin == 0)).sum())
    report = classification_report(y_test, pred, labels=labels, output_dict=True, zero_division=0)
    sources = [s for _, _, s in test_rows]
    by_source = {}
    for src in ("direct", "indirect"):
        idx = [i for i, s in enumerate(sources) if s == src and y_bin[i] == 1]
        by_source[src] = round(float(pred_bin[idx].mean()), 4) if idx else None

    metrics = {
        "binary_attack_vs_benign": {
            "precision": round(float(p), 4),
            "recall": round(float(r), 4),
            "f1": round(float(f), 4),
            "roc_auc": round(float(roc_auc_score(y_bin, attack_score)), 4),
            "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0,
            "recall_by_source": by_source,
        },
        "multiclass": {
            "accuracy": round(float(accuracy_score(y_test, pred)), 4),
            "macro_f1": round(float(f1_score(y_test, pred, average="macro")), 4),
            "per_class": {
                c: {
                    "precision": round(report[c]["precision"], 4),
                    "recall": round(report[c]["recall"], 4),
                    "f1": round(report[c]["f1-score"], 4),
                }
                for c in labels
            },
            "confusion_matrix": {
                "labels": labels,
                "matrix": confusion_matrix(y_test, pred, labels=labels).tolist(),
            },
        },
        "train_size": len(train_rows),
        "test_size": len(test_rows),
        "dataset": "synthetic templates (ml/data/injection.py); test = held-out templates",
        "extra_dataset": extra_csv.name if extra_csv else None,
    }

    model = {
        "format": "tfidf-logreg-v1",
        "version": VERSION,
        "analyzer": "char_wb",
        "classes": labels,
        "ngram_range": list(vec.ngram_range),
        "sublinear_tf": vec.sublinear_tf,
        "vocabulary": {t: int(i) for t, i in sorted(vec.vocabulary_.items())},
        "idf": [round(float(v), 6) for v in vec.idf_],
        "coef": [[round(float(v), 6) for v in row] for row in clf.coef_],
        "intercept": [round(float(v), 6) for v in clf.intercept_],
        "metrics": metrics,
        "training_data_sha256": hashlib.sha256(
            json.dumps(train_rows, ensure_ascii=False).encode()
        ).hexdigest(),
    }
    assert labels == CLASSES, labels  # noqa: S101
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--extra-csv", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(train(args.out, args.extra_csv), indent=2))
