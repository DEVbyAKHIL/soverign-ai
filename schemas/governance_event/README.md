# Governance event schema

`1.0.0.json` is the contract for every message on `governance.events.*`. It is a JSON Schema (draft 2020-12) with `additionalProperties: false` at the top level and in `metadata`, so an unexpected field fails validation rather than travelling downstream.

## Versioning

- **Patch** (1.0.x): wording and descriptions only; nothing a validator would treat differently.
- **Minor** (1.x.0): additive. Consumers ignore fields they don't know about. For example, adding an allowed enum value or a new optional `metadata` key.
- **Major** (x.0.0): anything else (removed or renamed fields, narrowed types). Requires a new file next to the old one; consumers handle both versions during a migration.

Every event carries `schema_version`, and the gateway's Kafka relay also sends it as a message header.

## What may never appear in an event

- Prompts, documents and model output.
- Detected entity values.
- Redaction maps.
- Credentials, API keys and tokens.
- Raw user ids. `caller_id` is a keyed HMAC pseudonym formatted as a UUID.

The schema enforces this structurally: there is no free-text field apart from bounded identifiers.
