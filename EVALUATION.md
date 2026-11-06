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

## Prompt-injection and intent detection (Phase 4)

Combined detector (rules + `injection-charlr-1.0.0` classifier, no LLM reviewer). Test: 1050 rows built from **held-out templates**
(seed 91): attacks in four classes, direct and embedded in documents, plus benign prompts and
look-alikes. Thresholds were tuned on a separate validation set (seed 333). Reproduce:
`python -m ml.evaluation.evaluate_injection` (results in `ml/evaluation/results/injection_eval.json`).

| Metric (attack vs benign, blocking labels only) | Value |
|---|---|
| Precision | **1.000** |
| Recall | **0.865** (direct 0.947, indirect 0.655) |
| F1 | **0.928** |
| False-positive rate | **0.000** |
| ROC-AUC (risk score) | 0.996 |
| Attacks routed to `suspicious` instead (alert only) | 12.2% |
| Benign prompts routed to `suspicious` (alert only) | 3.3% |

Recall by label: prompt_injection 0.90 · jailbreak 0.94 · system-prompt extraction 0.89 ·
data exfiltration **0.73** (the weakest class: many exfiltration phrasings look like ordinary requests).

| Robustness check | Result |
|---|---|
| Spec's named attacks plus variants (zero-width, leetspeak, base64, German, markdown beacon, indirect) | **13 / 13** flagged |
| Look-alike benign prompts ("ignore the noise", "what is a system prompt?", "enable developer mode on my phone") | **0 / 8** flagged |
| Technical attachments: code, config, CSV/JSON, prose (300) | 0% attack, 0% suspicious |
| Unseen format: log lines (300) | 0% attack, 0.3% suspicious |
| Hand-written knowledge-base sentences (10) | 0% attack, **30% suspicious** (alert noise) |
| Latency | p50 0.3 ms per prompt; 16 ms for 8,000 characters |

Design decisions this evaluation drove:
* **Technical text:** the first model labelled config snippets as exfiltration (0.86) and most logs or
  code as suspicious. Adding benign technical content, with one format held out, brought this to 0%.
* **Corroboration on documents:** short knowledge-base sentences like "Refunds are issued within
  14 days" scored as jailbreak, because the model had learned attack *vocabulary*. A 4% per-document
  false-positive rate would wrongly quarantine about 19% of 5-document RAG queries. Classifier-only
  hits on indirect content now need a rule signal or ≥ 0.80 confidence. That lowers indirect
  blocking recall from 0.79 to 0.66 (validation), but those attacks are still alerted as `suspicious`.
* **Default deny:** `allow + alert` policies for `suspicious` exposed a gap where an explicit allow
  bypassed default deny. That's fixed and tested.

Limits: synthetic English-centric data, a fixed payload vocabulary, and no adaptive attacker. Expect
lower recall against novel attacks. Enable the LLM reviewer for the `suspicious` band. Before relying on
these numbers, retrain with a vetted public dataset (`--extra-csv`) and real, labelled traffic.

## Agent behaviour anomaly detection (Phase 5)

Simulated agents (`ml/data/agents.py`), each with its own rate, working hours, payload size,
destinations, datasets, tool use and sensitive-data rate. Every agent gets a 7-day baseline. The
Isolation Forest was trained on normal windows from 120 agents (seed 10). Rule weights and forest
calibration were tuned on 60 *other* agents (seed 55). The results below are on 60 more agents never
used before (seed 99): 3960 windows, 30 normal and 36 anomalous per agent, nine anomaly
types at two severities. Reproduce: `python -m ml.evaluation.evaluate_anomaly`.

| Method | False-positive rate | Strong anomalies caught | Subtle anomalies caught | ROC-AUC |
|---|---|---|---|---|
| Isolation Forest alone | 0.6% | 6.7% | 3.8% | 0.799 |
| Statistical rules alone | 0.5% | 96.5% | 62.1% | 0.954 |
| **Combined (production)** | **0.7%** | **96.6%** | **63.2%** | **0.967** |

Detection rate of the combined detector by anomaly type:

| Anomaly | Strong | Subtle |
|---|---|---|
| burst | 1.00 | 0.96 |
| data volume | 0.98 | 0.93 |
| exfiltration | 1.00 | 1.00 |
| new destination | 1.00 | 1.00 |
| off hours | 0.93 | 0.80 |
| denial storm | 0.96 | 0.21 |
| dataset sweep | 1.00 | 0.63 |
| tool abuse | 0.97 | 0.08 |
| retry loop | 0.85 | 0.07 |

* **Explanations:** 97.1% of flagged strong anomalies name the expected reason.
* **Cold start** (< 1 day of history, absolute rules only): normal 0.0%
  flagged, denial storms 92.5%, retry loops 57.5%.
* **Latency:** p50 0.097 ms per evaluation (scoring only). Live agent requests end to end take about 16 ms.

What the evaluation changed:
* **Rule weights.** In the first version, each reason was named correctly but scored below the 0.7
  threshold on its own (new destination 0.4, off hours ≤ 0.6, retries ≤ 0.6). As a result, new-destination
  detection was 1% and off-hours 28%. All weights are now ≥ 0.70, and strengths ramp to full at a
  "clearly anomalous" level. On the held-out agents, strong-anomaly detection went from 62% to 97%
  and subtle from 35% to 63% (the retuning itself used only the validation agents).
* **The Isolation Forest adds little, and the reason is structural.** It only splits within the range of
  its training data, so a burst 4× beyond anything seen in training lands in the same leaf as the
  most extreme *normal* window. It can't express "far beyond normal", which is the most common incident.
  It is kept, as the spec requires, for multivariate combinations. It is calibrated at the 99.5th
  normal percentile, where it adds about 1 point of subtle-anomaly recall for +0.2 points FPR. A q99
  calibration doubled that FPR cost.

Limits: synthetic behaviour (real agents add destinations and datasets legitimately, so the
"any new destination" rule will flag them; expect reviews when agents are reconfigured). Subtle
denial storms (21%), tool abuse (8%) and retry loops (7%) are mostly missed by design: they're
within ~2× of normal. The model cannot see slow drift spread across many windows, or an adversary
who stays under the thresholds. The 7-day baseline includes anything an attacker did earlier, so
slow poisoning is possible.
