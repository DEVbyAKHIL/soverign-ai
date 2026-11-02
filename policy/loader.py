"""Load and version a set of policies from YAML files. Fails closed on any error."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from policy.validator import PolicyValidationError, parse_yaml, validate_policy

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MAX_POLICIES = 500


@dataclass(frozen=True)
class Policy:
    policy_id: str
    version: str
    description: str
    severity: str
    match: dict[str, Any]
    actions: tuple[str, ...]
    name: str
    route_to: dict[str, str] | None
    audit: dict[str, Any]
    enabled: bool
    source_yaml: str = field(repr=False)

    @property
    def ref(self) -> str:
        return f"{self.policy_id}@{self.version}"

    @property
    def yaml_sha256(self) -> str:
        return hashlib.sha256(self.source_yaml.encode("utf-8")).hexdigest()

    @classmethod
    def from_yaml(cls, text: str, source: str = "<policy>") -> "Policy":
        doc = parse_yaml(text, source)
        validate_policy(doc, source)
        return cls(
            policy_id=doc["policy_id"],
            version=doc["version"],
            description=doc["description"],
            severity=doc["severity"],
            match=doc["match"],
            actions=tuple(doc["actions"]),
            name=doc.get("name", doc["policy_id"]),
            route_to=doc.get("route_to"),
            audit={"retain_days": 365, "store_reason": True, **doc.get("audit", {})},
            enabled=doc.get("enabled", True),
            source_yaml=text,
        )


@dataclass(frozen=True)
class PolicySet:
    policies: tuple[Policy, ...]
    version: str

    @classmethod
    def build(cls, policies: list[Policy]) -> "PolicySet":
        ordered = sorted(policies, key=lambda p: (SEVERITY_RANK[p.severity], p.policy_id))
        digest = hashlib.sha256()
        for p in sorted(ordered, key=lambda p: p.policy_id):
            digest.update(f"{p.ref}:{p.yaml_sha256}\n".encode())
        return cls(policies=tuple(ordered), version=digest.hexdigest()[:16])

    def get(self, policy_id: str) -> Policy | None:
        return next((p for p in self.policies if p.policy_id == policy_id), None)


class PolicyLoadError(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("Refusing to load policies: " + " | ".join(errors))
        self.errors = errors


def load_policy_dir(directory: str | Path) -> PolicySet:
    path = Path(directory)
    if not path.is_dir():
        raise PolicyLoadError([f"policy directory not found: {path}"])
    files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml"))
    if len(files) > MAX_POLICIES:
        raise PolicyLoadError([f"too many policy files ({len(files)} > {MAX_POLICIES})"])

    errors: list[str] = []
    policies: list[Policy] = []
    seen: dict[str, str] = {}
    for f in files:
        try:
            policy = Policy.from_yaml(f.read_text(encoding="utf-8"), f.name)
        except PolicyValidationError as exc:
            errors.append(str(exc))
            continue
        if policy.policy_id in seen:
            errors.append(
                f"{f.name}: duplicate policy_id '{policy.policy_id}' "
                f"(also in {seen[policy.policy_id]})"
            )
            continue
        seen[policy.policy_id] = f.name
        if policy.enabled:
            policies.append(policy)

    if errors:
        raise PolicyLoadError(errors)
    if not policies:
        raise PolicyLoadError(["no enabled policies found"])
    return PolicySet.build(policies)
