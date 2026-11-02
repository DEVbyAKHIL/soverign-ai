from fastapi import APIRouter, Depends

from gateway.core.security import Principal, Scope, require_scopes
from gateway.dependencies import get_policy_set
from gateway.schemas.requests import PolicyValidateRequest
from gateway.schemas.responses import PolicyListOut, PolicyOut, PolicyValidateOut
from policy.loader import Policy, PolicySet
from policy.validator import PolicyValidationError

router = APIRouter(prefix="/v1/policies", tags=["policies"])


@router.get("", response_model=PolicyListOut)
async def list_policies(
    principal: Principal = Depends(require_scopes(Scope.POLICY_READ)),
    policy_set: PolicySet = Depends(get_policy_set),
) -> PolicyListOut:
    return PolicyListOut(
        policy_set_version=policy_set.version,
        policies=[
            PolicyOut(
                policy_id=p.policy_id,
                version=p.version,
                name=p.name,
                description=p.description,
                severity=p.severity,
                match=p.match,
                actions=list(p.actions),
                route_to=p.route_to,
                audit=p.audit,
                sha256=p.yaml_sha256,
            )
            for p in policy_set.policies
        ],
    )


@router.post("/validate", response_model=PolicyValidateOut)
async def validate(
    body: PolicyValidateRequest,
    principal: Principal = Depends(require_scopes(Scope.POLICY_WRITE)),
    policy_set: PolicySet = Depends(get_policy_set),
) -> PolicyValidateOut:
    """Validate a candidate policy without loading it (policies are deployed via Git)."""
    try:
        policy = Policy.from_yaml(body.policy_yaml, "candidate")
    except PolicyValidationError as exc:
        return PolicyValidateOut(valid=False, errors=exc.errors)

    loaded = policy_set.get(policy.policy_id)
    bump_needed = (
        loaded is not None
        and loaded.version == policy.version
        and loaded.yaml_sha256 != policy.yaml_sha256
    )
    errors = (
        [f"{policy.ref} is already loaded with different content; bump the version"]
        if bump_needed
        else []
    )
    return PolicyValidateOut(
        valid=not bump_needed,
        errors=errors,
        policy_id=policy.policy_id,
        version=policy.version,
        version_bump_required=bump_needed,
    )
