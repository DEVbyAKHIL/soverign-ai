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

Combined detector (rules + `injection-charlr-1.1.0` classifier, no LLM reviewer). Test: 1050 rows
built from **held-out templates** (seed 91): attacks in four classes, direct and embedded in
documents, plus benign prompts and look-alikes. Thresholds were tuned on a separate validation set
(seed 333). Reproduce: `python -m ml.evaluation.evaluate_injection` (results in
`ml/evaluation/results/injection_eval.json`).

| Metric (attack vs benign, blocking labels only) | 1.0.0 (synthetic only) | **1.1.0 (+ real data)** |
|---|---|---|
| Precision | 1.000 | **1.000** |
| Recall | 0.865 (direct 0.947, indirect 0.655) | **0.822** (direct 0.880, indirect 0.673) |
| F1 | 0.928 | **0.902** |
| False-positive rate | 0.000 | **0.000** |
| ROC-AUC (risk score) | 0.996 | 0.995 |
| Attacks routed to `suspicious` instead (alert only) | 12.2% | 16.8% |
| Benign prompts routed to `suspicious` (alert only) | 3.3% | 5.6% |

### Real data: deepset/prompt-injections

Version 1.1.0 adds the **train split** of
[deepset/prompt-injections](https://huggingface.co/datasets/deepset/prompt-injections) (Apache-2.0,
546 rows, English and German, 203 injections mapped to `prompt_injection`) to the synthetic corpus.
It is evaluated on that dataset's **test split** (116 rows, 60 injections), which is never trained
on. Reproduce: `python -m ml.evaluation.evaluate_real_injection` (results in
`ml/evaluation/results/injection_real_eval.json`; data in `ml/data/external/`).

| Real test split | 1.0.0 (synthetic only) | **1.1.0** |
|---|---|---|
| Recall (blocking labels) | 0.300 | **0.783** |
| Precision (blocking labels) | 0.947 | **1.000** |
| False-positive rate | 0.018 | **0.000** |
| Recall incl. `suspicious` (alerted) | 0.483 | **0.900** |
| ROC-AUC | 0.757 | **0.976** |

The synthetic-only model missed 70% of real attacks, so its synthetic scores reflected template fit
rather than real-world performance. Adding the real data more than doubles real recall. The cost:
on the synthetic hold-out, direct recall falls from 0.947 to 0.880 and
system-prompt extraction from 0.89 to 0.72, because the real
data has a single attack label. The named adversarial suite and look-alike checks are unchanged.
116 test rows is small, so treat the real-data figures as indicative (about ±8 points).

Recall by label (1.1.0): prompt_injection 0.89 · jailbreak 0.95 · system-prompt extraction 0.72 ·
data exfiltration **0.73** (the weakest class: many exfiltration phrasings look like ordinary requests).

| Robustness check | Result |
|---|---|
| Spec's named attacks plus variants (zero-width, leetspeak, base64, German, markdown beacon, indirect) | **13 / 13** flagged |
| Look-alike benign prompts ("ignore the noise", "what is a system prompt?", "enable developer mode on my phone") | **0 / 8** flagged |
| Technical attachments: code, config, CSV/JSON, prose (300) | 0% attack, 0% suspicious |
| Unseen format: log lines (300) | 0% attack, 2.7% suspicious (1.0.0: 0.3%) |
| Hand-written knowledge-base sentences (10) | 0% attack, **20% suspicious** (1.0.0: 30%; alert noise) |
| Latency | p50 0.4 ms per prompt; 23 ms for 8,000 characters |

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
these numbers, retrain with real, labelled traffic from your own deployment.

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

## Streaming data backend (Task 2, phases S1-S2)

All numbers below come from runs on one developer machine (Windows 11, Docker Desktop WSL2 VM
with 8 CPUs and ~8 GB RAM) on 2026-11-07, with the rest of the stack running (gateway, Ollama,
continuous producer at 10 events/s). They are single runs, not benchmarks.

### Ingestion: producer -> Kafka (`make verify-1m`, phase S1)

| Measure | Result |
|---|---|
| Events generated / acknowledged by Kafka | 1,000,000 / 1,000,000 |
| Read back from `governance.events.raw` | 1,000,000 |
| Valid against schema 1.0.0, valid UTC timestamps | 1,000,000 / 1,000,000 |
| Distinct `event_id`s / duplicates | 1,000,000 / 0 |
| Produce time | 124 s (about 8,000 messages/s, `acks=all`, idempotent producer) |
| Read-back + validation time | 458 s (single Python consumer validating every event) |
| Result | PASSED |

### End to end: Kafka -> Spark -> PostgreSQL with a processor kill (`make verify-pipeline`, phase S2)

Run 1 (2-minute watermark, `maxOffsetsPerTrigger=200000`): **FAILED**.

| Measure | Result |
|---|---|
| Produced / consumed / dead-letter | 1,000,000 / 1,000,000 / 0 |
| Aggregated | 916,437 |
| Missing (dropped as late by the watermark) | 83,563 (8.4%) |

Diagnosis: all missing events were the last ~33 s of the run on one Kafka partition (the three
tenants it carries each lost ~15%; the two tenants on other partitions were complete). Spark splits
`maxOffsetsPerTrigger` across partitions by offset count. Partitions that also carried the sparse
continuous traffic advanced further in event time per batch than the partition carrying only the
dense burst, the watermark followed the leading partitions, and the lagging partition's tail was
older than the 2-minute watermark. Fix: no offset cap by default and a 5-minute watermark;
late drops are now exported (`stream_rows_dropped_by_watermark`) and reported by the script.

Run 2 (5-minute watermark, no offset cap): **PASSED**.

| Measure | Result |
|---|---|
| Events produced (Kafka acks) | 1,000,000 |
| Events consumed (`validate` query) | 1,000,000 |
| Events aggregated (sum of 1-minute tumbling windows) | 1,000,000 |
| Same total from decisions-by-type (1 min), PII-per-model (5 min), sovereignty (10 min) | 1,000,000 each |
| Dead-letter events / late events | 0 / 0 |
| Sliding-window rows written (latency, injections, agent risk) | 4,575 |
| Processor killed (SIGKILL) and restarted at | 430,000 events consumed |
| Produce time / end-to-end until aggregates stable | 224 s / 516 s |
| Processor restart recovery | PASSED (no loss, no double counting) |

Not measured yet: peak memory per container, processor throughput without other load, behaviour
with dead-letter injection at 1M scale (covered at small scale by tests), multi-broker Kafka.

### Fresh-clone reproducibility (2026-11-07, commit `fc37095` + `cc65bcd`)

**Setup.**
- Cloned `feat/stream-phase1` from GitHub into an empty directory with no `.env`, run as a separate Compose project with new, empty volumes (containers, data and Kafka log all fresh).
- Only the README steps were followed, using the system Python with no virtualenv.
- Images were built with `--no-cache --pull`.
- Full log: `docs/evidence/fresh-clone-2026-11-07.log`.

| Step (README) | Result |
|---|---|
| `python scripts/init_env.py` | `.env` created, 10 generated secrets (values not printed); check OK |
| `docker compose build --no-cache --pull` | 2 min 27 s |
| `docker compose up --build -d` | All 10 services healthy within 1 min; `/readyz` ready; 9 migrations applied automatically; per-service DB roles created |
| `docker compose --profile test run --rm --build tests` | **749 passed**, 2 skipped (the Spark tests, which run in their own image) |
| `docker compose --profile test run --rm --build spark-tests` | **11 passed** |
| `python scripts/verify_pipeline.py --total 1000000` | First attempt: **FAILED**, a verifier bug (see below). After the fix (`cc65bcd`): **PASSED**. 1,000,000 produced = consumed = aggregated in all four tumbling aggregations, 0 dead-letter, 0 missing, processor killed at 550,867 and recovered; 541 s end to end (`docs/evidence/fresh-clone-verify-1m-2026-11-07.txt`) |
| `docker compose --profile ops run --rm retention --dry-run` | Runs as `retention_job`; 14 targets, 0 rows due (fresh data) |

**Verifier bug found by this test.** After the restart, one aggregation query (`requests_per_tenant_1m`) spent more than 60 s in a single catch-up batch. The old "unchanged for 60 s" rule ended the wait early and reported 312,810 events as late. Rechecked minute by minute, that query later reached the full count: **no event was lost**. The script now waits until every tumbling aggregation equals the valid count (15-minute drain timeout).

**Manual steps that remain:**

| Step | Why it can't be automated |
|---|---|
| Install Docker (with about 8 GB of RAM for its VM) and Python 3.10+ | Host prerequisites |
| Pull the Ollama models | Optional: only needed to forward approved requests to the local LLM. Not needed for the streaming pipeline, the tests or the verification |
| `COMPOSE_PROJECT_NAME=<other>` | Only when a second copy of the stack runs on the same machine (the Compose file pins the project name) |
| Docker Desktop on Windows with a moved disk image | The user account needs write access to the disk-image folder (see `docs/runbook.md`) |

**Removed manual steps:**
- Secret generation (`init_env.py`).
- Test-database creation (Postgres init script).
- Migrations and role creation (the `migrate` service).
- The `make` dependency on Windows (the README uses plain commands).
- A host virtualenv for the verification scripts (they need only the standard library).

### Local horizontal-scaling experiment (2026-11-07)

`python scripts/scaling_experiment.py --events 300000` ran on the fresh-clone stack: one producer, then **two concurrent producers**, each with fresh tenants and live event time. Raw samples: `docs/evidence/scaling-2026-11-07.json`.

**Host and setup.**
- One Windows 11 machine, Docker Desktop WSL2 VM with 8 vCPUs and about 8 GB of RAM.
- Single Kafka broker; Spark `local[4]`.
- Background traffic: the continuous producer at 10 events/s plus gateway health traffic.

| Metric | 1 producer | 2 producers | Change |
|---|---:|---:|---|
| Events | 300,000 | 600,000 | ×2 |
| Produce rate (Kafka acks, all producers) | 4,545 ev/s | 7,843 ev/s | **×1.73** |
| Pipeline throughput (events ÷ wall time to fully aggregated) | 1,669 ev/s | 2,308 ev/s | ×1.38 |
| Peak Spark validate rate (sampled, ~8 s interval) | 17,413 ev/s | 12,025 ev/s | Bursty, micro-batch |
| Peak Kafka lag (produced − consumed) | 245,845 | 575,788 | ×2.3 |
| Peak PostgreSQL write rate (aggregate inserts + updates) | 609 rows/s | 680 rows/s | |
| Aggregate rows written | 42,594 | 36,110 | Fewer windows, more events per window |
| Median end-to-end latency (50% produced → 50% aggregated) | 79.8 s | 145.1 s | |
| Completion latency (producers done → 100% aggregated) | 88.9 s | 160.7 s | |
| Events aggregated / dead-lettered / missing | 300,000 / 0 / 0 | 600,000 / 0 / 0 | |
| Peak CPU: Spark / Kafka / Postgres | 493% / 166% / 30% | 488% / 230% / 51% | % of one vCPU |
| Peak memory: Spark / Kafka / Postgres | 3,062 / 1,883 / 89 MiB | 3,069 / 2,156 / 93 MiB | |

**Interpretation.**
- Ingestion scales out: a second producer raised the acknowledged produce rate 1.73× with no loss. Kafka (6 partitions, one broker) was not the bottleneck.
- The single Spark process was. It stayed at about 5 vCPUs in both phases, so lag and latency grew with the input instead of the processing rate rising, and end-to-end throughput improved only 1.38×.
- Spark's memory sat at its 3 GiB container limit in both phases. It did not fail, but there is no headroom.
- PostgreSQL wrote at most about 700 rows/s at about 50% of one CPU, so the aggregate store is far from saturated.

**What this does and does not show.** This demonstrates **local horizontal scaling of the ingestion tier on one Docker host**. It is **not** evidence of production-cluster scalability. Not covered:
- multiple Kafka brokers or replication;
- Spark cluster mode with executors on several machines;
- isolated CPUs;
- repeated runs with variance.

Measurement caveats:
- These are single runs, sampled every ~8 s (peaks are approximate).
- `docker stats` memory includes page cache (Kafka's heap is 512 MB).
- The `docker compose run` producers also restart the one-shot `kafka-init` container, which shows CPU at start.

The next scaling steps would be Spark executors on more cores or hosts, more partitions with key salting for hot tenants, and a larger Spark memory limit.
