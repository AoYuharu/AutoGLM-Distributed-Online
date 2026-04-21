"""
Open-AutoGLM Distributed Server
Main application entry point
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import structlog

from src.config import settings
from src.database import init_db
from src.api import devices, tasks, ws  # logs暂时跳过 - 依赖已删除的模型
from src.services.websocket import ws_hub
from src.services.react_scheduler import scheduler
from src.services.action_router import action_router
from src.services.device_status_manager import device_status_manager
from src.schemas.schemas import HealthResponse
from src.logging_config import (
    get_api_logger,
    get_ws_logger,
    get_db_logger,
    file_handler as main_file_handler,
)

# Configure structlog for JSON console output
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

# Add file handler to root logger
root_logger = logging.getLogger()
root_logger.addHandler(main_file_handler)
root_logger.setLevel(getattr(logging, settings.LOG_LEVEL))

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info("Starting server", host=settings.HOST, port=settings.PORT)

    # Initialize database
    try:
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error("Database initialization failed", error=str(e))

    # Start WebSocket hub
    await ws_hub.start()
    logger.info("WebSocket hub started")

    # Initialize and start ActionRouter
    action_router._ws_hub = ws_hub
    await action_router.start()
    logger.info("ActionRouter started")

    # Start ReAct scheduler (thread pool)
    scheduler.set_ws_hub(ws_hub)
    scheduler.start()
    logger.info("ReAct scheduler started", core_threads=scheduler.core_threads, max_threads=scheduler.max_threads)

    # Start DeviceStatusManager offline checker daemon
    device_status_manager.start()
    logger.info("DeviceStatusManager offline checker started")

    yield

    # Shutdown
    logger.info("Shutting down server")
    scheduler.stop()
    logger.info("ReAct scheduler stopped")
    await action_router.stop()
    logger.info("ActionRouter stopped")
    await ws_hub.stop()
    logger.info("WebSocket hub stopped")


# Create FastAPI app
app = FastAPI(
    title="Open-AutoGLM Distributed Server",
    description="Web management interface for multi-device phone automation",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
_cors_origins = ["*"] if settings.CORS_ORIGINS == "*" else [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(devices.router)
app.include_router(tasks.router)
# logs router暂时跳过 - 依赖已删除的模型
# app.include_router(logs.router)
app.include_router(ws.router)


@app.get("/", tags=["root"])
async def root():
    """Root endpoint"""
    return {
        "name": "Open-AutoGLM Distributed Server",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check endpoint"""
    components = {}
    metrics = {}

    # Check database
    try:
        from sqlalchemy import text
        from src.database import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        components["database"] = "ok"
    except Exception as e:
        components["database"] = f"error: {str(e)}"

    # WebSocket status
    metrics["websocket_connections"] = ws_hub.connection_count
    metrics["websocket_registered_devices"] = ws_hub.registered_device_count

    # Overall status
    status = "healthy" if components.get("database") == "ok" else "degraded"

    return HealthResponse(
        status=status,
        components=components,
        metrics=metrics,
    )


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.HOST,
        port=settings.PORT,
        # reload=settings.DEBUG,
    )
