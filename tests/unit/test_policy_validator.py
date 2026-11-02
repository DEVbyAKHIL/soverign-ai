import pytest

from policy.loader import Policy
from policy.validator import PolicyValidationError, parse_yaml, validate_policy

VALID = """
policy_id: test_policy
version: 1.0.0
description: test
severity: high
match:
  destination_jurisdiction: [non_eu]
actions: [deny, alert]
"""


def _doc(**over):
    doc = parse_yaml(VALID)
    doc.update(over)
    return doc


def test_valid_policy_parses():
    p = Policy.from_yaml(VALID)
    assert p.ref == "test_policy@1.0.0"
    assert p.enabled is True
    assert p.audit["retain_days"] == 365


@pytest.mark.parametrize(
    "override,fragment",
    [
        ({"version": "1.0"}, "version"),
        ({"policy_id": "Bad-Id"}, "policy_id"),
        ({"severity": "extreme"}, "severity"),
        ({"actions": []}, "actions"),
        ({"actions": ["deny", "nuke"]}, "actions"),
        ({"match": {}}, "match"),
        ({"match": {"destination_jurisdiction": ["mars"]}}, "destination_jurisdiction"),
        ({"match": {"unknown_key": ["x"]}}, "match"),
        ({"match": {"min_risk_score": 1.5}}, "min_risk_score"),
        ({"extra_field": 1}, "<root>"),
        ({"audit": {"retain_days": 0}}, "retain_days"),
    ],
)
def test_schema_violations(override, fragment):
    with pytest.raises(PolicyValidationError) as exc:
        validate_policy(_doc(**override))
    assert any(fragment in e for e in exc.value.errors)


def test_missing_required_fields():
    with pytest.raises(PolicyValidationError) as exc:
        validate_policy({"policy_id": "abc"})
    assert len(exc.value.errors) >= 1


@pytest.mark.parametrize(
    "override,fragment",
    [
        ({"actions": ["allow", "deny"]}, "cannot be combined"),
        ({"actions": ["route_to_approved_model"]}, "requires a 'route_to'"),
        ({"route_to": {"model": "m", "jurisdiction": "eu"}}, "without the"),
        (
            {
                "actions": ["route_to_approved_model"],
                "route_to": {"model": "m", "jurisdiction": "unknown"},
            },
            "unknown jurisdiction",
        ),
        ({"actions": ["alert"]}, "enforcement action"),
    ],
)
def test_unsafe_policies_rejected(override, fragment):
    with pytest.raises(PolicyValidationError) as exc:
        validate_policy(_doc(**override))
    assert any(fragment in e for e in exc.value.errors)


@pytest.mark.parametrize("text", ["- a\n- b\n", "just a string", "", "key: [unclosed"])
def test_non_mapping_or_invalid_yaml(text):
    with pytest.raises(PolicyValidationError):
        parse_yaml(text)


def test_yaml_aliases_rejected():
    bomb = 'a: &a ["x","x"]\nb: &b [*a,*a]\nc: [*b,*b]\n'
    with pytest.raises(PolicyValidationError, match="anchors"):
        parse_yaml(bomb)


def test_python_tags_not_executed():
    with pytest.raises(PolicyValidationError):
        parse_yaml("policy_id: !!python/object/apply:os.system ['echo pwned']")


def test_oversized_policy_rejected():
    with pytest.raises(PolicyValidationError, match="exceeds"):
        parse_yaml("description: " + "x" * 70_000)
