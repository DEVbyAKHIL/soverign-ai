# Evaluation

All numbers come from **synthetic data** (`ml/data/synthetic.py`). It's template-based, so the scores
are an **upper bound**: real prompts are messier, and every result here must be re-measured on a
labelled sample of real (consented, minimised) traffic before production use.

Reproduce (runs on Linux with NER, via Docker):

```bash
make evaluate                                         # PII detection -> ml/evaluation/results/pii_eval.json
python -m ml.training.train_sensitivity_classifier    # retrain + held-out metrics for the classifier
```

## PII and sensitive-data detection (Phase 3)

Setup: 1,000 synthetic documents with gold spans (seed 11) and 300 PII-free documents including
near-misses (seed 12). A prediction counts as a true positive when it overlaps a gold span of the
same type. Pipeline: `regex-1.0.0+terms-2`, `presidio-2.2.366+en_core_web_sm-3.8.0`.

| Entity | Precision | Recall | F1 | Support | False positives |
|---|---|---|---|---|---|
| PERSON | 1.000 | 0.964 | 0.982 | 415 | 0 |
| EMAIL | 1.000 | 1.000 | 1.000 | 187 | 0 |
| PHONE | 1.000 | 1.000 | 1.000 | 142 | 0 |
| NATIONAL_ID | 1.000 | 1.000 | 1.000 | 135 | 0 |
| FINANCIAL | 1.000 | 1.000 | 1.000 | 121 | 0 |
| PASSWORD | 1.000 | 1.000 | 1.000 | 81 | 0 |
| LOCATION | 0.890 | 0.936 | 0.912 | 78 | 9 |
| MEDICAL | 1.000 | 1.000 | 1.000 | 72 | 0 |
| DATE_OF_BIRTH | 1.000 | 1.000 | 1.000 | 70 | 0 |
| INTERNAL_PROJECT | 1.000 | 1.000 | 1.000 | 70 | 0 |
| PASSPORT | 1.000 | 1.000 | 1.000 | 69 | 0 |
| ADDRESS | 1.000 | 1.000 | 1.000 | 67 | 0 |
| API_KEY | 1.000 | 1.000 | 1.000 | 65 | 0 |
| IBAN | 1.000 | 1.000 | 1.000 | 61 | 0 |
| CREDIT_CARD | 1.000 | 1.000 | 1.000 | 51 | 0 |
| CUSTOMER_ID | 1.000 | 1.000 | 1.000 | 51 | 0 |
| ORGANISATION | n/a | n/a | n/a | 0 | 129 |

* **Micro average:** precision 0.926, recall 0.989, **F1 0.956**. All of the precision loss comes from
  ORGANISATION, which has no gold labels in this dataset: spaCy tags tokens such as "IBAN" or "SSN"
  as organisations. ORGANISATION carries no sensitive category, never raises the classification and
  is never transformed, so these false positives don't change any decision.
* **PII-free documents:** 0.0% contained a detection that would raise sensitivity. 5.3% contained an
  ORGANISATION-only detection.
* **Latency, 8,000-character inputs, NER on:** p50 97 ms, p95 107 ms, max 134 ms. The target is
  under 150 ms. Removing unused spaCy components (parser, tagger, lemmatizer) cut p95 from 153 ms, and
  pre-filtering phone candidates cut phone matching from 69 ms to 7 ms.

Issues found and fixed during evaluation:
* PERSON precision rose from **0.85 to 1.00** after ignoring single-word instruction verbs
  ("Summarise", "Please", …) that the small spaCy model mislabels at the start of a sentence.
  The list is versioned in `detection/config/sensitive_terms.yaml`.
* The MEDICAL false positives (43) came from counting "diagnosed" as a separate entity. It now acts
  only as a context word that raises confidence.
* "Missed" UK NI numbers turned out to be generator bugs: NK/NT/TN prefixes are never issued, so the
  detector was right to reject them.

## Sensitivity classifier

TF-IDF (1–2 grams) + multinomial logistic regression, `sens-linear-1.0.0`. Training: 2,000 rows. Test: 500
rows built from **held-out templates**, phrasings that never appear in training, so the test
measures generalisation rather than memorisation of the generator.

| Class | Precision | Recall | F1 | False-positive rate |
|---|---|---|---|---|
| public | 0.47 | 0.56 | 0.51 | 0.155 |
| internal | 0.49 | 0.38 | 0.43 | 0.098 |
| confidential | 1.00 | 1.00 | 1.00 | 0.000 |
| restricted | 0.83 | 0.60 | 0.70 | 0.030 |
| high_risk | 0.75 | 1.00 | 0.86 | 0.083 |

**Accuracy 0.708, macro F1 0.700.** Confusion matrix (rows = true, columns = predicted, order
confidential/high_risk/internal/public/restricted):
`[[100,0,0,0,0],[0,100,0,0,0],[0,0,38,62,0],[0,0,32,56,12],[0,33,7,0,60]]`.

With exact templates in the test set the model scored 100%, which is meaningless. The held-out-template
score above is the honest one.

How the gateway uses it: the classifier can only **raise** a classification, and only when its
confidence is at least 0.80. On the held-out set, 36 of 500 predictions cleared that bar and all
36 were correct. Public/internal confusion has no effect, because the classifier never lowers a
declared classification and the policies don't distinguish the two. Detected entities, not the
classifier, drive the decisions that matter.
