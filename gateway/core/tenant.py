"""Tenant-aware data access. Every tenant-owned query must go through TenantScope."""

import uuid
from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.errors import TenantIsolationError

T = TypeVar("T")


class TenantScope:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    @staticmethod
    def _require_tenant_column(model: type) -> Any:
        column = getattr(model, "tenant_id", None)
        if column is None:
            raise TypeError(f"{model.__name__} is not tenant-scoped (no tenant_id column)")
        return column

    def select(self, model: type[T]) -> Select[tuple[T]]:
        return select(model).where(self._require_tenant_column(model) == self.tenant_id)

    def apply(self, stmt: Select, model: type) -> Select:
        return stmt.where(self._require_tenant_column(model) == self.tenant_id)

    async def get(self, model: type[T], obj_id: uuid.UUID) -> T:
        stmt = self.select(model).where(model.id == obj_id)  # type: ignore[attr-defined]
        obj = (await self.session.execute(stmt)).scalar_one_or_none()
        if obj is None:
            raise TenantIsolationError("Resource not found")
        return obj

    def add(self, obj: Any) -> None:
        self._require_tenant_column(type(obj))
        if obj.tenant_id is None:
            obj.tenant_id = self.tenant_id
        elif obj.tenant_id != self.tenant_id:
            raise TenantIsolationError("Resource not found")
        self.session.add(obj)
