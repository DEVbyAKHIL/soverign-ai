"""Record the loaded policy set in the `policies` table (policy versioning).

Run after migrations: ``python -m policy.sync``.

* A new (policy_id, version) is inserted and becomes the active version.
* An existing (policy_id, version) whose YAML has changed is rejected: changing a
  policy requires bumping its version, so every decision maps to exact policy text.
* Policies no longer on disk are marked inactive (never deleted).
"""

import asyncio
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import get_settings
from gateway.db import dispose_engine, get_session
from gateway.logging_config import configure_logging, get_logger
from gateway.models import Policy as PolicyRow
from policy.loader import PolicyLoadError, PolicySet, load_policy_dir

SYSTEM_ACTOR = uuid.UUID("00000000-0000-0000-0000-000000000000")
log = get_logger(__name__)


async def sync_policies(session: AsyncSession, policy_set: PolicySet) -> dict[str, int]:
    stats = {"inserted": 0, "unchanged": 0, "deactivated": 0}
    rows = (await session.execute(select(PolicyRow))).scalars().all()
    by_key = {(r.policy_id, r.version): r for r in rows}

    conflicts = [
        p.ref
        for p in policy_set.policies
        if (row := by_key.get((p.policy_id, p.version))) and row.policy_yaml != p.source_yaml
    ]
    if conflicts:
        raise PolicyLoadError(
            [f"{ref}: content changed without a version bump" for ref in conflicts]
        )

    current = {(p.policy_id, p.version) for p in policy_set.policies}
    for p in policy_set.policies:
        if (p.policy_id, p.version) in by_key:
            stats["unchanged"] += 1
            continue
        session.add(
            PolicyRow(
                policy_id=p.policy_id,
                version=p.version,
                name=p.name,
                description=p.description,
                severity=p.severity,
                policy_yaml=p.source_yaml,
                is_active=True,
                created_by=SYSTEM_ACTOR,
            )
        )
        stats["inserted"] += 1

    stale = [r.id for r in rows if r.is_active and (r.policy_id, r.version) not in current]
    if stale:
        await session.execute(
            update(PolicyRow).where(PolicyRow.id.in_(stale)).values(is_active=False)
        )
        stats["deactivated"] = len(stale)
    reactivate = [r.id for r in rows if not r.is_active and (r.policy_id, r.version) in current]
    if reactivate:
        await session.execute(
            update(PolicyRow).where(PolicyRow.id.in_(reactivate)).values(is_active=True)
        )
    await session.commit()
    return stats


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    policy_set = load_policy_dir(settings.policy_dir)
    async for session in get_session():
        stats = await sync_policies(session, policy_set)
    log.info("policies_synced", policy_set_version=policy_set.version, **stats)
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
