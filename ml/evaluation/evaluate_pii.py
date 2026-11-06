"""Evaluate the detection pipeline on seeded synthetic data.

    python -m ml.evaluation.evaluate_pii [--docs 1000] [--negatives 300] [--out PATH]

Matching: a prediction is a true positive if it overlaps a gold span of the same entity type.
Seeds differ from anything used in tests, but the generator is template-based, so these numbers
are an upper bound for real-world text (see docs/model-cards.md).
"""

import argparse
import json
import os
import random
import statistics
import time
from collections import Counter
from pathlib import Path

from detection.pipeline import DetectionPipeline
from detection.types import FLOOR_MIN_CONFIDENCE
from ml.data.synthetic import negative_documents, pii_documents

OUT = Path(__file__).resolve().parent / "results" / "pii_eval.json"


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def evaluate(n_docs: int, n_neg: int, ner_mode: str) -> dict:
    pipe = DetectionPipeline(ner_mode=ner_mode)  # type: ignore[arg-type]
    tp, fp, fn = Counter(), Counter(), Counter()

    for doc in pii_documents(n_docs, seed=11):
        preds = pipe.analyze([doc.text], "public").entities
        matched_gold: set[int] = set()
        for p in preds:
            hit = next(
                (
                    i
                    for i, g in enumerate(doc.spans)
                    if g.entity_type == p.entity_type and _overlap(p.start, p.end, g.start, g.end)
                ),
                None,
            )
            if hit is None:
                fp[p.entity_type] += 1
            else:
                tp[p.entity_type] += 1
                matched_gold.add(hit)
        for i, g in enumerate(doc.spans):
            if i not in matched_gold:
                fn[g.entity_type] += 1

    per_type = {}
    for t in sorted(set(tp) | set(fp) | set(fn)):
        p = tp[t] / (tp[t] + fp[t]) if tp[t] + fp[t] else 0.0
        r = tp[t] / (tp[t] + fn[t]) if tp[t] + fn[t] else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        per_type[t] = {
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
            "support": tp[t] + fn[t],
            "false_positives": fp[t],
        }

    total_tp, total_fp, total_fn = sum(tp.values()), sum(fp.values()), sum(fn.values())
    micro_p = total_tp / (total_tp + total_fp) if total_tp + total_fp else 0.0
    micro_r = total_tp / (total_tp + total_fn) if total_tp + total_fn else 0.0

    # False-positive rate on PII-free documents.
    negatives = negative_documents(n_neg, seed=12)
    any_hit = raised = 0
    neg_types: Counter[str] = Counter()
    for doc in negatives:
        ents = pipe.analyze([doc.text], "public").entities
        if ents:
            any_hit += 1
            neg_types.update(e.entity_type for e in ents)
        if any(e.confidence >= FLOOR_MIN_CONFIDENCE and e.spec.categories for e in ents):
            raised += 1

    # Latency on ~8,000-character inputs.
    rng = random.Random(5)  # noqa: S311
    pool = [d.text for d in pii_documents(400, seed=21)] + [d.text for d in negatives]
    latencies = []
    for _ in range(100):
        text = ""
        while len(text) < 8000:
            text += rng.choice(pool) + " "
        start = time.perf_counter()
        pipe.analyze([text[:8000]], "internal")
        latencies.append((time.perf_counter() - start) * 1000)
    latencies.sort()

    return {
        "ner_available": pipe.ner.available,
        "model_versions": pipe.model_versions,
        "documents": n_docs,
        "negative_documents": n_neg,
        "micro": {
            "precision": round(micro_p, 4),
            "recall": round(micro_r, 4),
            "f1": round(2 * micro_p * micro_r / (micro_p + micro_r), 4)
            if micro_p + micro_r
            else 0.0,
        },
        "per_entity": per_type,
        "negatives": {
            "docs_with_any_detection": round(any_hit / n_neg, 4),
            "docs_with_sensitive_detection_fpr": round(raised / n_neg, 4),
            "detected_types": dict(neg_types),
        },
        "latency_ms_8000_chars": {
            "p50": round(statistics.median(latencies), 2),
            "p95": round(latencies[int(len(latencies) * 0.95) - 1], 2),
            "max": round(latencies[-1], 2),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=1000)
    parser.add_argument("--negatives", type=int, default=300)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    result = evaluate(args.docs, args.negatives, os.environ.get("NER_MODE", "optional"))
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    except OSError:
        pass  # read-only filesystem (e.g. container); stdout already has the result
