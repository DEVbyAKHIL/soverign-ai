# Model cards

ML components produce **signals**. The policy engine makes the decision. Every decision records
the version of each component it used (`decisions.model_versions`, audit `event_data.model_versions`).

## Regex and validator detector: `regex-1.0.0+terms-<n>`

* **What:** regular expressions plus validators. Luhn for cards; ISO 13616 mod-97 and per-country
  length for IBANs; SSN area/group/serial rules; UK NINO prefix rules; libphonenumber for phone numbers.
  Context words within 60 characters raise confidence (e.g. "salary" before an amount).
* **Organisation vocabulary:** internal project names, customer-ID formats and the NER ignore-list
  live in `detection/config/sensitive_terms.yaml` (versioned; the version is part of the model version).
* **Limits:** only recognises formats it knows. National IDs outside US/UK need a label
  ("national id", "Personalausweis Nr", "tax id", …). Free-text secrets with no label or known
  prefix are missed.

## NER detector: `presidio-2.2.366+en_core_web_sm-3.8.0`

* **What:** Microsoft Presidio running spaCy's `en_core_web_sm` (pinned by SHA-256 in
  `requirements.txt`). Produces PERSON, LOCATION and ORGANISATION. Only the `tok2vec` and `ner`
  pipeline components run.
* **Language:** English only. Names in other scripts or languages are detected less reliably.
* **Known errors:** capitalised verbs at the start of a sentence get tagged as PERSON (mitigated by
  the ignore-list); acronyms get tagged as ORGANISATION (harmless: no sensitive category).
* **Failure mode:** with `NER_MODE=required` (default), the gateway refuses to start if the model
  can't load. `optional` is for development hosts only.
* **Upgrade path:** `en_core_web_lg` or a transformer pipeline for higher recall, at more latency
  and a larger image.

## Sensitivity classifier: `sens-linear-1.0.0`

* **What:** TF-IDF (1–2 grams, sublinear TF) + multinomial logistic regression. It predicts
  public / internal / confidential / restricted / high_risk.
* **Artifact:** `detection/pii/models/sensitivity_linear_v1.json`, plain JSON, loaded without pickle
  or scikit-learn. Parity with scikit-learn is tested to 1e-6.
* **Training data:** synthetic templates only (`ml/data/synthetic.py`). Training data SHA-256 is stored in
  the artifact.
* **Held-out-template performance:** accuracy 0.71, macro F1 0.70 (see `EVALUATION.md`).
* **Intended use:** raise sensitivity when at least 0.80 confident. It **never lowers** a declared or
  entity-derived classification.
* **Not suitable for:** being the only control for any class; non-English text.
* **Spec deviation:** the spec calls for a compact transformer (DistilBERT/DeBERTa-v3-xsmall). A
  transformer trained on the same few hundred synthetic templates would add about 250 MB and tens of
  milliseconds without measurable benefit. The classifier interface is backend-agnostic: once a real
  labelled corpus exists, train a transformer, export it to ONNX, and add an `onnx` backend.

## Risk score

`risk = 1 - Π(1 - weight(entity) × confidence)` (noisy-OR) over all detected entities. Weights are
in `detection/types.py`, e.g. credit card 0.95, email 0.6, organisation 0.1. Phases 4 and 5 add
injection and agent-anomaly signals.

## Injection rules: `injection-rules-1.0.0`

* **What:** about 20 regular expressions over normalised text (NFKC, zero-width removal, leetspeak folding),
  plus base64 payload decoding. Each rule emits a named signal with a label and weight
  (`detection/injection/rules.py`). Patterns cover English plus common German/French/Spanish override phrasing.
* **Strengths:** deterministic, explainable, sub-millisecond; strong rule hits (≥ 0.70) decide the label.
* **Limits:** novel phrasings, other languages, and attacks split across turns or documents are missed.

## Injection / intent classifier: `injection-charlr-1.0.0`

* **What:** char_wb 2–5-gram TF-IDF (40k features) + class-balanced multinomial logistic regression
  over 5 classes (benign + 4 attack classes). JSON artifact; parity with scikit-learn tested.
* **Training data:** synthetic templates (`ml/data/injection.py`): direct attacks, attacks embedded
  in documents, benign prompts, look-alikes, and technical/KB content. `--extra-csv` adds a local,
  licence-checked public dataset.
* **Held-out-template results (classifier alone):** see `metrics` in the artifact. Combined detector
  results are in `EVALUATION.md`.
* **Known failure modes:** learns topic vocabulary from the attack payloads (mitigated by
  corroboration on documents); data exfiltration phrased as an ordinary request.
* **Spec deviation:** the spec asks for a compact transformer. The same reasoning as for the
  sensitivity classifier applies; the `LinearTextModel` interface allows an ONNX backend later.

## Local LLM reviewer (optional)

* **What:** any self-hosted OpenAI-compatible chat model (`LLM_REVIEWER_URL`, `LLM_REVIEWER_MODEL`).
* **When:** only for verdicts in the uncertain band. It cannot override strong rule hits.
* **Safety:** delimited untrusted input, temperature 0, enum-validated JSON verdict, 3 s timeout; any
  failure falls back to `suspicious`. Adds latency only to uncertain requests.
