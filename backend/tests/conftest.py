from collections.abc import AsyncGenerator
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine, AsyncEngine, AsyncConnection, AsyncTransaction,
)
from sqlalchemy.orm import configure_mappers
from sqlalchemy.pool import NullPool

import app.core.models  # noqa: F401
from app.core.config import settings
from app.core.db import get_async_session
from app.core.enums import RoleName

from main import app

from app.users import crud as users_crud

configure_mappers()

DATABASE_URL: str = (
    settings.DATABASE_URL.get_secret_value()
    if hasattr(settings.DATABASE_URL, "get_secret_value")
    else settings.DATABASE_URL
)

test_engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    join_transaction_mode="create_savepoint",
)


@pytest.fixture(autouse=True)
def isolate_upload_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> Path:
    """
    Redirects photo uploads to a temporary folder during tests.
    """
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    return tmp_path


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Isolated database session: any `commit()` within the API becomes a `SAVEPOINT`
    and is rolled back.
    """
    connection: AsyncConnection = await test_engine.connect()
    transaction: AsyncTransaction = await connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    await session.close()
    await transaction.rollback()
    await connection.close()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    HTTP client with a spoofed database session.
    """
    async def _override_get_async_session() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise
    
    app.dependency_overrides[get_async_session] = _override_get_async_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def make_user_headers(client: AsyncClient, db_session: AsyncSession):
    """
    A factory for creating a user with a specified system role and obtaining a JWT.
    """
    async def _factory(
        email: str,
        role: RoleName = RoleName.OWNER,
        password: str = "StrongPass123!",
    ) -> dict[str, str]:
        await users_crud.create_user(
            db_session,
            user_name=email.split("@")[0],
            user_email=email,
            password=password,
            initial_role_names=(role.value,),
        )
        await db_session.flush()

        resp: Response = await client.post(
            "/api/v1/users/auth/token",
            data={
                "username": email,
                "password": password
            },
        )
        assert resp.status_code == 200, f"Failed to obtain token: {resp.text}"
        token = resp.json()["access_token"]
        return {
            "Authorization": f"Bearer {token}"
        }

    return _factory
