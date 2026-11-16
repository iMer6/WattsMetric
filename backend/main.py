import time

import logging
from logging import Logger
from contextlib import asynccontextmanager

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from app.core.config import settings

from app.services.health import check_database_connection

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logger: Logger = logging.getLogger("watts_metric.main")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Application lifecycle: initialization at startup
    """
    logger.info("Running the WattsMetric Backend API")
    yield
    logger.info("Stopping WattsMetric Backend API")

app: FastAPI = FastAPI(
    title=settings.PROJECT_NAME + " API",
    description=f"Backend API for the {settings.PROJECT_NAME} "
                f"power generation system monitoring software system",
    version=settings.VERSION,
    lifespan=lifespan,
)

START_TIME: float = time.time()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=[
        "Authorization",
        "Content-Type",
    ],
    expose_headers=["Content-Disposition", "X-Total-Count"],
    max_age=86400,
)

@app.get("/", tags=["Root"])
def root() -> dict[str, Any]:
    env: str = settings.ENVIRONMENT
    return {
        "status": "ok",
        "message": f"{settings.PROJECT_NAME} API is working successfully",
        "version": settings.VERSION,
        "environment": env,
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "links": {
            "docs": "/docs" if env != "production" else None,
            "redoc": "/redoc" if env != "production" else None,
            "openapi": "/openapi.json" if env != "production" else None,
            "health": "/health",
        }
    }

@app.get(
    "/api/v1/health",
    tags=["Service"],
    response_model=None
)
async def health() -> dict | JSONResponse:
    """
    Checking API availability and database connection.
    """
    from sqlalchemy import text
    from app.core.db import async_session_maker as session_maker
    
    database_ok: bool = True
    try:
        async with session_maker() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        database_ok = False
    
    if not database_ok:
        return JSONResponse(
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "database": database_ok,
            }
        )
    
    return {
        "status": "ok" if database_ok else "unhealthy",
        "database": database_ok,
    }

@app.get("/health")
async def health_check():
    db_is_connected: bool = await check_database_connection()
    if not db_is_connected:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "database": "disconnected",
            }
        )
    
    return {
        "status": "healthy",
        "database": "connected",
    }