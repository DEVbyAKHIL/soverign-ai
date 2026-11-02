import pytest
import yaml

from gateway.config import get_settings
from policy.engine import DEFAULT_ALLOW, DEFAULT_DENY, EvaluationContext, evaluate
from policy.loader import Policy, PolicySet, load_policy_dir


@pytest.fixture(scope="module")
def repo_policies() -> PolicySet:
    return load_policy_dir(get_settings().policy_dir)


def ctx(**over) -> EvaluationContext:
    base = dict(
        caller_type="user",
        destination_jurisdiction="eu",
        data_classification="internal",
        requested_model="m",
        declared_purpose="p",
    )
    base.update(over)
    return EvaluationContext(**base)


def _policy(pid: str, actions: list[str], match: dict, sev="high", route_to=None) -> Policy:
    body = {
        "policy_id": pid,
        "version": "1.0.0",
        "description": pid,
        "severity": sev,
        "match": match,
        "actions": actions,
    }
    if route_to:
        body["route_to"] = route_to
    return Policy.from_yaml(yaml.safe_dump(body))


# --- repository policies -------------------------------------------------


def test_internal_to_eu_allowed_by_default(repo_policies):
    d = evaluate(ctx(), repo_policies)
    assert (d.final_decision, d.action_taken, d.decisive_policy) == (
        "allow",
        "allow",
        DEFAULT_ALLOW,
    )
    assert d.matched_policies == ()


def test_confidential_to_non_eu_denied(repo_policies):
    d = evaluate(
        ctx(
            data_classification="confidential",
            destination_jurisdiction="non_eu",
            data_sensitivity=frozenset({"confidential"}),
        ),
        repo_policies,
    )
    assert d.final_decision == "deny"
    assert d.decisive_policy == "block_pii_to_non_eu_models@1.2.0"
    assert set(d.side_effects) == {"alert", "create_review_case"}
    assert d.requires_review
    assert "destination_jurisdiction=non_eu" in d.explanation


def test_pii_to_non_eu_denied(repo_policies):
    d = evaluate(
        ctx(destination_jurisdiction="non_eu", data_sensitivity=frozenset({"pii"})), repo_policies
    )
    assert d.final_decision == "deny"


def test_confidential_to_unknown_routed_to_eu(repo_policies):
    d = evaluate(
        ctx(data_classification="confidential", destination_jurisdiction="unknown"), repo_policies
    )
    assert d.final_decision == "allow_with_modification"
    assert d.action_taken == "route_to_approved_model"
    assert d.route_to == {"model": "eu-approved-llm", "jurisdiction": "eu"}
    assert "eu-approved-llm" in d.explanation


def test_high_risk_requires_review(repo_policies):
    d = evaluate(ctx(data_classification="high_risk"), repo_policies)
    assert (d.final_decision, d.action_taken) == ("review", "require_human_review")
    assert d.requires_review


@pytest.mark.parametrize(
    "label",
    [
        "prompt_injection",
        "jailbreak_attempt",
        "system_prompt_extraction_attempt",
        "data_exfiltration_attempt",
    ],
)
def test_attack_intents_denied(repo_policies, label):
    d = evaluate(ctx(intent_label=label), repo_policies)
    assert d.final_decision == "deny"
    assert d.decisive_policy == "block_system_prompt_extraction@1.0.0"


def test_unknown_destination_without_policy_is_default_denied(repo_policies):
    d = evaluate(ctx(destination_jurisdiction="unknown"), repo_policies)
    assert (d.final_decision, d.decisive_policy) == ("deny", DEFAULT_DENY)


def test_deny_beats_review_when_both_match(repo_policies):
    d = evaluate(
        ctx(
            data_classification="high_risk",
            destination_jurisdiction="non_eu",
            data_sensitivity=frozenset({"pii"}),
        ),
        repo_policies,
    )
    assert d.action_taken == "deny"
    assert set(d.matched_policies) == {
        "block_pii_to_non_eu_models@1.2.0",
        "require_review_high_risk@1.0.0",
    }
    assert "Also matched" in d.explanation


def test_trace_covers_every_policy(repo_policies):
    d = evaluate(ctx(), repo_policies)
    assert d.policies_evaluated == len(repo_policies.policies) == len(d.trace)


def test_deterministic(repo_policies):
    c = ctx(data_classification="high_risk", destination_jurisdiction="non_eu")
    assert evaluate(c, repo_policies) == evaluate(c, repo_policies)


# --- engine semantics ----------------------------------------------------


def test_allow_cannot_override_deny():
    ps = PolicySet.build(
        [
            _policy("allow_everything", ["allow"], {"caller_type": ["user"]}, sev="critical"),
            _policy("deny_non_eu", ["deny"], {"destination_jurisdiction": ["non_eu"]}, sev="low"),
        ]
    )
    assert evaluate(ctx(destination_jurisdiction="non_eu"), ps).action_taken == "deny"


def test_transforms_applied_only_when_not_blocked():
    ps = PolicySet.build(
        [
            _policy("redact_internal", ["redact"], {"data_classification": ["internal"]}),
            _policy("mask_users", ["mask"], {"caller_type": ["user"]}),
        ]
    )
    d = evaluate(ctx(), ps)
    assert d.action_taken == "redact"
    assert d.transforms == ("redact", "mask")

    ps2 = PolicySet.build(
        [*ps.policies, _policy("deny_all_users", ["deny"], {"caller_type": ["user"]})]
    )
    assert evaluate(ctx(), ps2).transforms == ()


def test_and_semantics_within_policy():
    ps = PolicySet.build(
        [
            _policy(
                "two_conditions",
                ["deny"],
                {"caller_type": ["agent"], "destination_jurisdiction": ["non_eu"]},
            )
        ]
    )
    assert evaluate(ctx(caller_type="agent"), ps).action_taken == "allow"
    assert (
        evaluate(ctx(caller_type="agent", destination_jurisdiction="non_eu"), ps).action_taken
        == "deny"
    )


def test_score_thresholds():
    ps = PolicySet.build(
        [
            _policy("risky", ["require_human_review"], {"min_risk_score": 0.8}),
            _policy("anomalous", ["quarantine"], {"min_agent_anomaly_score": 0.9}),
        ]
    )
    assert evaluate(ctx(risk_score=0.79), ps).action_taken == "allow"
    assert evaluate(ctx(risk_score=0.8), ps).action_taken == "require_human_review"
    d = evaluate(ctx(risk_score=0.95, agent_anomaly_score=0.95), ps)
    assert (d.action_taken, d.final_decision) == ("quarantine", "deny")


def test_stage_defaults_to_request():
    ps = PolicySet.build(
        [
            _policy("resp_only", ["deny"], {"stage": ["response"]}),
            _policy("req_default", ["redact"], {"caller_type": ["user"]}),
        ]
    )
    assert evaluate(ctx(), ps).action_taken == "redact"
    assert evaluate(ctx(stage="response"), ps).action_taken == "deny"


def test_entity_types_set_match():
    ps = PolicySet.build([_policy("cards", ["mask"], {"entity_types": ["CREDIT_CARD", "IBAN"]})])
    assert evaluate(ctx(entity_types=frozenset({"EMAIL"})), ps).action_taken == "allow"
    d = evaluate(ctx(entity_types=frozenset({"EMAIL", "IBAN"})), ps)
    assert d.action_taken == "mask"
    assert "entity_types contains IBAN" in d.explanation


def test_explicit_allow_carries_side_effects():
    ps = PolicySet.build(
        [_policy("allow_and_alert", ["allow", "alert"], {"caller_type": ["user"]})]
    )
    d = evaluate(ctx(), ps)
    assert d.action_taken == "allow" and d.side_effects == ("alert",)
