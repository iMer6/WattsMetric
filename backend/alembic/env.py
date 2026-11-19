import asyncio
import sys

from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import pool, MetaData
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config, AsyncEngine

from alembic import context
from alembic.config import Config

# backend
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from app.core.config import settings
from app.core.db import Base

from app.users.models import *

config: Config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata: MetaData = Base.metadata


def run_migrations_offline() -> None:
    url: str | None = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata
    )
    
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable: AsyncEngine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

# Create migration: alembic revision --autogenerate -m "Migration message"
# Update DB: alembic upgrade head
# Current: alembic current
# Migration history: alembic history --verbose
# Rollback last migration: alembic downgrade -t
