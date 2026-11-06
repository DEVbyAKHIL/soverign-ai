"""Policy schema and safety validation.

A policy that fails validation is never loaded. Validation has two layers:
1. JSON Schema (structure, types, enums, no unknown keys).
2. Semantic safety rules that a schema cannot express.
"""

from typing import Any

import yaml
from jsonschema import Draft202012Validator

MAX_POLICY_BYTES = 64 * 1024

ACTIONS = (
    "allow",
    "deny",
    "redact",
    "mask",
    "pseudonymise",
    "route_to_approved_model",
    "require_human_review",
    "alert",
    "quarantine",
    "create_review_case",
)
SEVERITIES = ("low", "medium", "high", "critical")
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted", "high_risk")
JURISDICTIONS = ("eu", "non_eu", "unknown")
CALLER_TYPES = ("user", "service", "agent")
INTENT_LABELS = (
    "benign",
    "suspicious",
    "prompt_injection",
    "jailbreak_attempt",
    "data_exfiltration_attempt",
    "system_prompt_extraction_attempt",
)
STAGES = ("request", "response")

_str_list = {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}


def _enum_list(values: tuple[str, ...]) -> dict:
    return {"type": "array", "items": {"enum": list(values)}, "minItems": 1, "uniqueItems": True}


_score = {"type": "number", "minimum": 0, "maximum": 1}

POLICY_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["policy_id", "version", "description", "severity", "match", "actions"],
    "properties": {
        "policy_id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{2,63}$"},
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "name": {"type": "string", "maxLength": 200},
        "description": {"type": "string", "minLength": 1, "maxLength": 1000},
        "severity": {"enum": list(SEVERITIES)},
        "enabled": {"type": "boolean"},
        "match": {
            "type": "object",
            "additionalProperties": False,
            "minProperties": 1,
            "properties": {
                "stage": _enum_list(STAGES),
                "data_sensitivity": _str_list,
                "data_classification": _enum_list(CLASSIFICATIONS),
                "destination_jurisdiction": _enum_list(JURISDICTIONS),
                "caller_type": _enum_list(CALLER_TYPES),
                "requested_model": _str_list,
                "declared_purpose": _str_list,
                "entity_types": _str_list,
                "intent_label": _enum_list(INTENT_LABELS),
                "indirect_intent_label": _enum_list(INTENT_LABELS),
                "min_risk_score": _score,
                "min_agent_anomaly_score": _score,
            },
        },
        "actions": {
            "type": "array",
            "items": {"enum": list(ACTIONS)},
            "minItems": 1,
            "uniqueItems": True,
        },
        "route_to": {
            "type": "object",
            "additionalProperties": False,
            "required": ["model", "jurisdiction"],
            "properties": {
                "model": {"type": "string", "minLength": 1},
                "jurisdiction": {"enum": list(JURISDICTIONS)},
            },
        },
        "audit": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "retain_days": {"type": "integer", "minimum": 1, "maximum": 3650},
                "store_reason": {"type": "boolean"},
            },
        },
    },
}

_validator = Draft202012Validator(POLICY_SCHEMA)

BLOCKING = {"deny", "quarantine"}


class PolicyValidationError(ValueError):
    def __init__(self, errors: list[str], source: str = "<policy>") -> None:
        super().__init__(f"{source}: " + "; ".join(errors))
        self.errors = errors
        self.source = source


def parse_yaml(text: str, source: str = "<policy>") -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_POLICY_BYTES:
        raise PolicyValidationError([f"policy exceeds {MAX_POLICY_BYTES} bytes"], source)
    # Aliases/anchors enable "billion laughs" style expansion; policies never need them.
    if "&" in text and "*" in text and _uses_aliases(text):
        raise PolicyValidationError(["YAML anchors and aliases are not allowed"], source)
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyValidationError([f"invalid YAML: {exc.__class__.__name__}"], source) from exc
    if not isinstance(doc, dict):
        raise PolicyValidationError(["policy document must be a mapping"], source)
    return doc


def _uses_aliases(text: str) -> bool:
    try:
        return any(isinstance(tok, yaml.AliasToken | yaml.AnchorToken) for tok in yaml.scan(text))
    except yaml.YAMLError:
        return False


def validate_policy(doc: dict[str, Any], source: str = "<policy>") -> None:
    """Raise PolicyValidationError listing every problem found."""
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(_validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        raise PolicyValidationError(errors, source)

    actions = set(doc["actions"])
    semantic: list[str] = []
    if "allow" in actions and actions & BLOCKING:
        semantic.append("'allow' cannot be combined with 'deny' or 'quarantine'")
    if "route_to_approved_model" in actions and "route_to" not in doc:
        semantic.append("'route_to_approved_model' requires a 'route_to' block")
    if "route_to" in doc and "route_to_approved_model" not in actions:
        semantic.append("'route_to' given without the 'route_to_approved_model' action")
    if "route_to" in doc and doc["route_to"]["jurisdiction"] == "unknown":
        semantic.append("cannot route to an endpoint of unknown jurisdiction")
    if actions <= {"alert", "create_review_case"}:
        semantic.append("policy needs at least one enforcement action besides alert/review case")
    if semantic:
        raise PolicyValidationError(semantic, source)
