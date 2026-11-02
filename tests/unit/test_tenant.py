import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from gateway.core.errors import TenantIsolationError
from gateway.core.tenant import TenantScope
from gateway.models import Agent, Decision, Policy


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_select_adds_tenant_filter(tenant_a):
    scope = TenantScope(MagicMock(), tenant_a)
    sql = _sql(scope.select(Decision))
    assert "decisions.tenant_id = " in sql


def test_non_tenant_model_rejected(tenant_a):
    with pytest.raises(TypeError):
        TenantScope(MagicMock(), tenant_a).select(Policy)


async def test_get_missing_raises_not_found(tenant_a):
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    with pytest.raises(TenantIsolationError):
        await TenantScope(session, tenant_a).get(Agent, uuid.uuid4())


def test_add_sets_tenant(tenant_a):
    session = MagicMock()
    agent = Agent(name="a", owner_id=uuid.uuid4(), agent_type="rag")
    TenantScope(session, tenant_a).add(agent)
    assert agent.tenant_id == tenant_a
    session.add.assert_called_once_with(agent)


def test_add_cross_tenant_rejected(tenant_a, tenant_b):
    session = MagicMock()
    agent = Agent(tenant_id=tenant_b, name="a", owner_id=uuid.uuid4(), agent_type="rag")
    with pytest.raises(TenantIsolationError):
        TenantScope(session, tenant_a).add(agent)
    session.add.assert_not_called()
