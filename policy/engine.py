"""Deterministic policy evaluation.

Rules:
* Within a policy, every ``match`` key must hold (AND). List keys match when the
  context value (or any of its values, for set-valued fields) is in the list.
* All matching policies contribute actions. The decisive action is chosen by a
  fixed precedence, so an ``allow`` can never override a ``deny``.
* If nothing enforceable matches, a built-in default applies: deny for unknown
  destinations or ``high_risk`` data, allow otherwise.
* Evaluation is pure: same context + same PolicySet -> same decision.
"""

from dataclasses import dataclass, field
from typing import Any

from policy.loader import Policy, PolicySet

# Higher wins.
PRECEDENCE = {
    "deny": 100,
    "quarantine": 90,
    "require_human_review": 80,
    "route_to_approved_model": 70,
    "redact": 60,
    "pseudonymise": 50,
    "mask": 40,
    "allow": 10,
}
SIDE_EFFECTS = ("alert", "create_review_case")
TRANSFORMS = ("pseudonymise", "redact", "mask")

FINAL_DECISION = {
    "deny": "deny",
    "quarantine": "deny",
    "require_human_review": "review",
    "route_to_approved_model": "allow_with_modification",
    "pseudonymise": "allow_with_modification",
    "redact": "allow_with_modification",
    "mask": "allow_with_modification",
    "allow": "allow",
}

DEFAULT_DENY = "builtin.default_deny_unknown_or_high_risk"
DEFAULT_ALLOW = "builtin.default_allow"


@dataclass(frozen=True)
class EvaluationContext:
    caller_type: str
    destination_jurisdiction: str
    data_classification: str
    requested_model: str
    declared_purpose: str
    stage: str = "request"
    data_sensitivity: frozenset[str] = frozenset()
    entity_types: frozenset[str] = frozenset()
    intent_label: str = "benign"
    risk_score: float = 0.0
    agent_anomaly_score: float = 0.0


@dataclass(frozen=True)
class PolicyDecision:
    final_decision: str
    action_taken: str
    actions: tuple[str, ...]
    side_effects: tuple[str, ...]
    transforms: tuple[str, ...]
    transform_plan: tuple[tuple[str, frozenset[str] | None], ...]
    matched_policies: tuple[str, ...]
    decisive_policy: str
    route_to: dict[str, str] | None
    requires_review: bool
    policy_set_version: str
    explanation: str
    policies_evaluated: int
    trace: tuple[dict[str, Any], ...] = field(default=())


_LIST_KEYS = {
    "stage": "stage",
    "data_classification": "data_classification",
    "destination_jurisdiction": "destination_jurisdiction",
    "caller_type": "caller_type",
    "requested_model": "requested_model",
    "declared_purpose": "declared_purpose",
    "intent_label": "intent_label",
}
_SET_KEYS = {"data_sensitivity": "data_sensitivity", "entity_types": "entity_types"}


def _matches(policy: Policy, ctx: EvaluationContext) -> tuple[bool, list[str]]:
    """Return (matched, reasons). Reasons explain each satisfied condition."""
    m = policy.match
    reasons: list[str] = []
    # Policies apply to the request stage unless they say otherwise.
    stages = m.get("stage", ["request"])
    if ctx.stage not in stages:
        return False, []
    for key, attr in _LIST_KEYS.items():
        if key == "stage" or key not in m:
            continue
        value = getattr(ctx, attr)
        if value not in m[key]:
            return False, []
        reasons.append(f"{key}={value}")
    for key, attr in _SET_KEYS.items():
        if key not in m:
            continue
        hit = sorted(getattr(ctx, attr) & set(m[key]))
        if not hit:
            return False, []
        reasons.append(f"{key} contains {','.join(hit)}")
    if "min_risk_score" in m:
        if ctx.risk_score < m["min_risk_score"]:
            return False, []
        reasons.append(f"risk_score {ctx.risk_score:.2f} >= {m['min_risk_score']}")
    if "min_agent_anomaly_score" in m:
        if ctx.agent_anomaly_score < m["min_agent_anomaly_score"]:
            return False, []
        reasons.append(
            f"agent_anomaly_score {ctx.agent_anomaly_score:.2f} >= {m['min_agent_anomaly_score']}"
        )
    return True, reasons


def evaluate(ctx: EvaluationContext, policy_set: PolicySet) -> PolicyDecision:
    matched: list[tuple[Policy, list[str]]] = []
    trace: list[dict[str, Any]] = []
    for policy in policy_set.policies:
        ok, reasons = _matches(policy, ctx)
        trace.append({"policy": policy.ref, "matched": ok, "reasons": reasons})
        if ok:
            matched.append((policy, reasons))

    actions: set[str] = set()
    for policy, _ in matched:
        actions.update(policy.actions)

    enforceable = [a for a in actions if a in PRECEDENCE]
    side_effects = tuple(a for a in SIDE_EFFECTS if a in actions)

    if enforceable:
        decisive = max(enforceable, key=lambda a: PRECEDENCE[a])
        # Deterministic: policies are pre-sorted by (severity, policy_id).
        decisive_policy = next(p for p, _ in matched if decisive in p.actions)
        decisive_ref = decisive_policy.ref
        route_to = decisive_policy.route_to if decisive == "route_to_approved_model" else None
        explanation = _explain(decisive, decisive_policy, matched)
    elif ctx.destination_jurisdiction == "unknown" or ctx.data_classification == "high_risk":
        decisive, decisive_ref, route_to = "deny", DEFAULT_DENY, None
        explanation = (
            "Denied by default: no policy explicitly covers a request with "
            f"destination_jurisdiction={ctx.destination_jurisdiction} and "
            f"data_classification={ctx.data_classification}."
        )
    else:
        decisive, decisive_ref, route_to = "allow", DEFAULT_ALLOW, None
        explanation = "Allowed: no policy restricts this request."

    # Transforms only apply when the request is not blocked or held.
    proceeds = FINAL_DECISION[decisive] in ("allow", "allow_with_modification")
    transforms = tuple(t for t in TRANSFORMS if t in actions) if proceeds else ()
    # Each transforming policy applies to the entity types it matched on, or to all
    # sensitive entities when it matched on something broader (e.g. data_sensitivity).
    transform_plan = tuple(
        (t, frozenset(p.match["entity_types"]) if "entity_types" in p.match else None)
        for p, _ in matched
        for t in p.actions
        if t in TRANSFORMS and proceeds
    )
    requires_review = decisive == "require_human_review" or "create_review_case" in side_effects

    return PolicyDecision(
        final_decision=FINAL_DECISION[decisive],
        action_taken=decisive,
        actions=tuple(sorted(actions)),
        side_effects=side_effects,
        transforms=transforms,
        transform_plan=transform_plan,
        matched_policies=tuple(p.ref for p, _ in matched),
        decisive_policy=decisive_ref,
        route_to=route_to,
        requires_review=requires_review,
        policy_set_version=policy_set.version,
        explanation=explanation,
        policies_evaluated=len(policy_set.policies),
        trace=tuple(trace),
    )


def _explain(action: str, policy: Policy, matched: list[tuple[Policy, list[str]]]) -> str:
    reasons = next(r for p, r in matched if p is policy)
    verb = {
        "deny": "Blocked",
        "quarantine": "Quarantined",
        "require_human_review": "Held for human review",
        "route_to_approved_model": "Routed to an approved model",
        "pseudonymise": "Allowed after pseudonymisation",
        "redact": "Allowed after redaction",
        "mask": "Allowed after masking",
        "allow": "Allowed",
    }[action]
    text = f"{verb} by policy {policy.ref} ({policy.description}). Conditions met: " + (
        "; ".join(reasons) or "none"
    )
    if action == "route_to_approved_model" and policy.route_to:
        text += f". Destination: {policy.route_to['model']} ({policy.route_to['jurisdiction']})"
    others = [p.ref for p, _ in matched if p is not policy]
    if others:
        text += f". Also matched: {', '.join(others)}"
    return text + "."
