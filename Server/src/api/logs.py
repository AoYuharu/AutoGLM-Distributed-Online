"""
Log API routes

Provides endpoints for retrieving device and task logs.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database import get_db
from src.models.models import Device, LogEntry, Task, TaskStep, Screenshot
from src.schemas.schemas import LogEntryResponse, LogListResponse
from src.logging_config import get_api_logger
import structlog

api_logger = get_api_logger()
router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


@router.get("/{device_id}", response_model=LogListResponse)
async def get_device_logs(
    device_id: str,
    level: Optional[str] = Query(None, description="Filter by log level (info, success, warning, error)"),
    log_type: Optional[str] = Query(None, description="Filter by log type"),
    task_id: Optional[str] = Query(None, description="Filter by task ID"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of logs to return"),
    offset: int = Query(0, ge=0, description="Number of logs to skip"),
    db: Session = Depends(get_db),
):
    """
    Get logs for a specific device.

    Args:
        device_id: The device ID to get logs for
        level: Optional filter by log level
        log_type: Optional filter by log type
        task_id: Optional filter by task ID
        limit: Maximum number of logs to return (default 100)
        offset: Number of logs to skip (default 0)

    Returns:
        LogListResponse with log entries
    """
    # Find device by device_id
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

    query = select(LogEntry).where(LogEntry.device_id == device.id)

    if level:
        query = query.where(LogEntry.level == level)
    if log_type:
        query = query.where(LogEntry.log_type == log_type)
    if task_id:
        # Find task by task_id
        task = db.execute(select(Task).where(Task.task_id == task_id)).scalar_one_or_none()
        if task:
            query = query.where(LogEntry.task_id == task.id)

    # Order by created_at descending
    query = query.order_by(LogEntry.created_at.desc())

    # Get total count
    total = len(db.execute(query).scalars().all())

    # Apply pagination
    query = query.limit(limit).offset(offset)
    logs = db.execute(query).scalars().all()

    log_responses = [
        LogEntryResponse(
            id=log.id,
            device_id=device_id,
            task_id=log.task_id,
            log_type=log.log_type,
            level=log.level,
            message=log.message,
            details=log.details,
            screenshot_url=log.screenshot_url,
            created_at=log.created_at,
        )
        for log in logs
    ]

    return LogListResponse(logs=log_responses, total=total)


@router.get("/{device_id}/task_screenshots")
async def get_device_task_screenshots(
    device_id: str,
    db: Session = Depends(get_db),
):
    """
    Get task screenshots for a specific device.

    Returns observe results and screenshots from tasks for the device.

    Args:
        device_id: The device ID

    Returns:
        List of task steps with screenshots
    """
    # Find device
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

    # Get all tasks for this device
    tasks = db.execute(
        select(Task).where(Task.device_id == device.id).order_by(Task.created_at.desc())
    ).scalars().all()

    result = []
    for task in tasks:
        # Get task steps with screenshots
        steps = db.execute(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.screenshot_url.isnot(None)
            ).order_by(TaskStep.step_number)
        ).scalars().all()

        for step in steps:
            result.append({
                "task_id": task.task_id,
                "step_number": step.step_number,
                "action_type": step.action_type,
                "action_params": step.action_params,
                "thinking": step.thinking,
                "screenshot_url": step.screenshot_url,
                "success": step.success,
                "error": step.error,
                "created_at": step.created_at.isoformat() if step.created_at else None,
            })

    return {
        "device_id": device_id,
        "screenshots": result,
        "total": len(result),
    }
