import logging
import time
from logging import Logger
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
# from app.core.db import async_session_maker

from app.users.router import router as users_router
from app.sources.router import router as sources_router
from app.iot.router import router as iot_router
from app.telemetry.router import router as telemetry_router
from app.maintenance.router import router as maintenance_router

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logger: Logger = logging.getLogger("watts_metric")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle: initialization at startup
    """
    logger.info("Running the WattsMetric Backend API")
    
    # try:
    #     async with async_session_maker() as session:
    #         await bootstrap(session)
    # except Exception:
    #     logger.exception("Failed to bootstrap the database")
    
    yield
    logger.info("Stopping WattsMetric Backend API")

app: FastAPI = FastAPI(
    title="WattsMetric API",
    description="Backend API for the WattsMetric "
                "power generation system monitoring software system",
    version="0.0.1",
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

# TODO: add parameters dependencies and responses for include_router()

app.include_router(
    users_router,
    prefix="/api/v1/users",
    tags=["Users & Auth"]
)
app.include_router(
    sources_router,
    prefix="/api/v1/sources",
    tags=["Sources"]
)
app.include_router(
    iot_router,
    prefix="/api/v1/iot",
    tags=["IoT Devices"]
)
app.include_router(
    telemetry_router,
    prefix="/api/v1/telemetry",
    tags=["Telemetry & Measurement"]
)
app.include_router(
    maintenance_router,
    prefix="/api/v1/maintenance",
    tags=["Maintenance"]
)

@app.get("/", tags=["Root"])
def root() -> dict:
    return {
        "status": "ok",
        "message": "WattsMetric API is working successfully",
        "version": app.version,
        "environment": "development", # TODO: get value from os.getenv
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "links": {
            "docs": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json",
            "health": "/health",
        }
    }

@app.get("api/v1/health", tags=["Service"])
async def health() -> dict:
    """
    Checking API availability and database connection.
    """
    from sqlalchemy import text
    from app.core.db import async_session_maker as session_maker
    
    database_ok: bool = True
    try:
        async with session_maker() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        database_ok = False
        raise
    
    return {
        "status": "ok" if database_ok else "degraded",
        "database": database_ok,
    }

@app.get("/health")
def health_check():
    db_is_connected: bool = check_database()
    if not db_is_connected:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",}
        )
    return {
        "status": "healthy",
    }