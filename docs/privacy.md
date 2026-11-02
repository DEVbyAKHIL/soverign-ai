# Privacy and data handling

## What is stored

| Data | Where | Form |
|---|---|---|
| Prompt / attachments / embedding inputs | nowhere | Only a SHA-256 fingerprint, character count and part count go into the audit event |
| Detected values | `detections.entity_value_hash`, audit redaction map | **Keyed** HMAC-SHA256 (`ENTITY_HASH_KEY`, per-tenant), never the raw value |
| Entity metadata | `detections`, audit event | type, confidence, detector, model version, part, offsets, risk level |
| Transformed content | API response only | Not persisted |
| Redaction map | audit event | offsets, entity type, transform, replacement (masked values recorded as `[masked]`), value hash |

The hashes are keyed because unkeyed SHA-256 of a card number or phone number can be reversed by
brute force: the input space is small.

## Transforms

| Policy action | Result | Reversible | Linkable |
|---|---|---|---|
| `redact` | `[REDACTED:EMAIL]` | no | no |
| `mask` | `j***@example.com`, `**** **** **** 1234` | no | partly (last 4) |
| `pseudonymise` | `<PERSON_3f9a1c2b>`; cards, IBANs, phones and IDs become format-preserving tokens | no (keyed one-way) | yes, within one tenant |

* Pseudonyms are deterministic per tenant, so the same person maps to the same pseudonym and a model
  can still reason about "the same customer". They aren't linkable across tenants.
* Card tokens keep the last 4 digits and are forced to **fail** the Luhn check, so they can never be
  mistaken for (or used as) a real card. IBAN tokens fail the mod-97 check.
* When several policies apply to one entity, the strongest transform wins: redact > pseudonymise > mask.

## Keys

* `ENTITY_HASH_KEY` is separate from `JWT_SECRET`. Purpose-specific keys are derived from it with HMAC
  (`entity-hash`, `pseudonym`).
* Rotating it changes future hashes and pseudonyms. Existing audit events remain verifiable, because
  the hash chain covers the stored values, not the key.

## Retention

Audit retention is configured with `AUDIT_RETENTION_DAYS` (default 365) and per-policy `audit.retain_days`.
Automated deletion is not implemented yet; it has to coexist with the append-only audit trigger
(planned as a privileged, audited archival job).

## Development data

All test and evaluation data is synthetic. Card numbers use test prefixes; IBANs, SSNs and NINOs are
random but valid in format.
