import logging
from logging import Logger

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import async_session_maker

logger: Logger = logging.getLogger("watts_metric.health")


async def check_database_connection() -> bool:
    try:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, Exception) as e:
        logger.error(f"Database health check failed: {e}")
        return False
