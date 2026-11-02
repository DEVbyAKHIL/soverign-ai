import asyncio
import os
import uuid

# Database tests run only when TEST_DATABASE_URL points at a disposable *_test database.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ["DATABASE_URL"] = (
    TEST_DATABASE_URL or "postgresql+asyncpg://test:test@localhost:5432/unused_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import asyncpg  # noqa: E402
import fakeredis  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from gateway.core.security import create_access_token  # noqa: E402
from gateway.main import create_app  # noqa: E402
from gateway.redis_client import set_redis  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _raw_dsn() -> str:
    return TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def _exec(sql: str) -> None:
    conn = await asyncpg.connect(_raw_dsn())
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_db():
    """Rebuild the test database from scratch using the real Alembic migrations."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    if not TEST_DATABASE_URL.rsplit("/", 1)[-1].endswith("_test"):
        pytest.exit("Refusing to run: TEST_DATABASE_URL database name must end with '_test'")
    asyncio.run(_exec("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "migrations"))
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def clean_db(migrated_db):
    # TRUNCATE bypasses the row-level append-only trigger; only used to reset tests.
    asyncio.run(_exec("TRUNCATE audit_events, detections, decisions, policies CASCADE"))
    return migrated_db


async def raw_sql(sql: str, *args):
    conn = await asyncpg.connect(_raw_dsn())
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


@pytest.fixture
def fake_redis():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    set_redis(client)
    yield client
    set_redis(None)


@pytest.fixture
def app(fake_redis):
    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def tenant_a() -> uuid.UUID:
    return uuid.UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def tenant_b() -> uuid.UUID:
    return uuid.UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture
def make_token(tenant_a):
    def _make(role: str = "developer", tenant: uuid.UUID | None = None) -> str:
        return create_access_token(subject=uuid.uuid4(), tenant_id=tenant or tenant_a, role=role)

    return _make
