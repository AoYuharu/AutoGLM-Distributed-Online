"""
Device Sessions API - Simplified

Contains:
- POST /observe: Receive observe results from clients (refactored)
- GET /api/v1/devices/{device_id}/session: Get device session from memory
- GET /api/v1/devices/{device_id}/chat: Get chat history from file storage
- POST /api/v1/devices/{device_id}/chat: Add chat message to file storage
- DELETE /api/v1/devices/{device_id}/chat: Clear chat history from file storage
- DELETE /api/v1/devices/{device_id}/session-context: Clear session context (memory + file)
- GET /api/v1/devices/{device_id}/history: Get ReAct records
- POST /api/v1/devices/{device_id}/interrupt: Interrupt device task
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database import get_db
from src.models.models import Device
from src.schemas.schemas import (
    ApiResponse,
    DeviceChatHistoryResponse,
    DeviceTaskSessionResponse,
    ObserveResultMessage,
)
from src.logging_config import get_api_logger, get_network_logger
from src.services.react_scheduler import scheduler
from src.services.device_status_manager import device_status_manager
from src.services.file_storage import file_storage
import structlog

api_logger = get_api_logger()
network_logger = get_network_logger()
logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["device_sessions"])


def _build_chat_message_response(message: dict):
    progress_message = message.get("progress_message", message.get("message"))
    content = message.get("content")
    if content is None:
        content = progress_message or message.get("result") or message.get("error") or ""

    return {
        "id": message.get("id", ""),
        "role": message.get("role", ""),
        "content": content,
        "thinking": message.get("thinking"),
        "action_type": message.get("action_type"),
        "action_params": message.get("action_params"),
        "screenshot_path": message.get("screenshot_path"),
        "created_at": message.get("created_at"),
        "task_id": message.get("task_id"),
        "session_id": message.get("session_id"),
        "run_id": message.get("run_id"),
        "step_number": message.get("step_number"),
        "phase": message.get("phase"),
        "stage": message.get("stage"),
        "progress_status_text": message.get("progress_status_text"),
        "progress_message": progress_message,
        "result": message.get("result"),
        "success": message.get("success"),
        "error": message.get("error"),
        "error_type": message.get("error_type"),
        "version": message.get("version"),
        "error_code": message.get("error_code"),
        "data": message.get("data"),
    }


@router.post("/observe", response_model=ApiResponse)
async def receive_observe_result(
    message: ObserveResultMessage,
    db: Session = Depends(get_db),
):
    result = await handle_observe_result_http(message.model_dump(), db)
    return ApiResponse(**result)


async def handle_observe_result_http(message: dict, db: Session) -> dict:
    from src.services.action_router import action_router

    payload = message.get("payload", {})
    task_id = payload.get("task_id")
    device_id = payload.get("device_id")
    step_number = payload.get("step_number")
    screenshot = payload.get("screenshot")
    result = payload.get("result", "")
    success = payload.get("success", True)
    error = payload.get("error")
    round_version = payload.get("version")
    if round_version is None:
        round_version = message.get("version")
        try:
            round_version = int(round_version) if round_version not in (None, "", "1.0") else None
        except (TypeError, ValueError):
            round_version = None

    api_logger.info(
        f"[observe_result_http] Received - task_id={task_id}, device_id={device_id}, "
        f"step={step_number}, version={round_version}, success={success}"
    )
    network_logger.info(
        f"[observe_result] Received task_id={task_id}, device_id={device_id}, "
        f"step={step_number}, version={round_version}, success={success}"
    )

    screenshot_path = None

    # Save screenshot to file storage if provided
    if screenshot and device_id and step_number is not None:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = file_storage.save_screenshot(device_id, step_number, ts, screenshot)
            api_logger.info(f"[observe_result] Screenshot saved: {screenshot_path}")
        except Exception as e:
            api_logger.error(f"[observe_result] Failed to save screenshot: {e}")

    if device_id:
        try:
            file_storage.append_adb_log(device_id, {
                "type": "observe_result",
                "task_id": task_id,
                "step_number": step_number,
                "version": round_version,
                "success": success,
                "error": error,
                "result": result,
                "screenshot": screenshot_path,
            })
        except Exception as e:
            api_logger.error(f"[observe_result] Failed to append adb log: {e}")

    # Update scheduler with observe result
    if device_id:
        await scheduler.set_observe_result(
            device_id,
            screenshot or "",
            result or error or "",
            step_number=step_number,
            round_version=round_version,
            screenshot_path=screenshot_path,
            success=success,
            error=error,
        )

    # Let action router handle the observe result, except bootstrap screenshots
    handled = False
    is_bootstrap_observe = step_number == 0
    if action_router and round_version is not None and device_id and not is_bootstrap_observe:
        handled = await action_router.handle_observe_result(
            {
                **payload,
                "version": round_version,
            }
        )

    return {
        "success": True,
        "message": "Observe result received" if handled else "Observe result recorded",
    }


@router.get("/devices/{device_id}/session", response_model=DeviceTaskSessionResponse)
async def get_device_task_session(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Get device task session from memory (scheduler)"""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Get active task from scheduler
    active_task = scheduler.get_task(device_id)

    if active_task and active_task.is_active:
        # Load chat history from file storage
        chat_history = file_storage.load_chat_history(device_id)
        latest_screenshot = None

        # Get latest screenshot
        screenshots = file_storage.get_screenshots(device_id)
        if screenshots:
            latest_screenshot = f"screenshots/{screenshots[-1]}"

        # Get session metadata from scheduler's session registry
        session_meta = scheduler._device_sessions.get(device_id, {})
        session_run_count = session_meta.get("run_count", 1)
        session_started_at_iso = None
        if session_meta.get("session_started_at"):
            session_started_at_iso = datetime.fromtimestamp(session_meta["session_started_at"]).isoformat()
        run_started_at_iso = None
        if active_task.run_started_at:
            run_started_at_iso = datetime.fromtimestamp(active_task.run_started_at).isoformat()

        return DeviceTaskSessionResponse(
            device_id=device_id,
            task_id=active_task.task_id,
            session_id=active_task.session_id or active_task.task_id,
            run_id=active_task.run_id,
            status=active_task.status.value,
            instruction=active_task.instruction,
            current_step=active_task.current_step,
            max_steps=active_task.max_steps,
            max_observe_error_retries=active_task.max_observe_error_retries,
            consecutive_observe_error_count=active_task.consecutive_observe_error_count,
            awaiting_observe_error_decision=active_task.is_waiting_observe_error_decision(),
            pending_observe_error_message=active_task.get_latest_error_reason(),
            pending_observe_error_prompt=active_task.get_observe_error_prompt_payload(),
            latest_screenshot=latest_screenshot,
            interruptible=True,
            latest_error_reason=active_task.get_latest_error_reason(),
            session_started_at=session_started_at_iso,
            run_started_at=run_started_at_iso,
            session_run_count=session_run_count,
            chat_history=[_build_chat_message_response(msg) for msg in chat_history[-50:]],
        )

    # No active task, return idle state — include session metadata if session exists
    session_meta = scheduler._device_sessions.get(device_id, {})
    idle_session_id = session_meta.get("session_id")
    idle_session_started_at_iso = None
    if session_meta.get("session_started_at"):
        idle_session_started_at_iso = datetime.fromtimestamp(session_meta["session_started_at"]).isoformat()
    idle_session_run_count = session_meta.get("run_count", 0)

    return DeviceTaskSessionResponse(
        device_id=device_id,
        task_id=None,
        session_id=idle_session_id,
        run_id=None,
        status="idle",
        instruction=None,
        current_step=0,
        max_steps=0,
        max_observe_error_retries=0,
        consecutive_observe_error_count=0,
        awaiting_observe_error_decision=False,
        pending_observe_error_message=None,
        pending_observe_error_prompt=None,
        latest_screenshot=None,
        interruptible=False,
        latest_error_reason=None,
        session_started_at=idle_session_started_at_iso,
        run_started_at=None,
        session_run_count=idle_session_run_count,
        chat_history=[],
    )


@router.get("/devices/{device_id}/chat", response_model=DeviceChatHistoryResponse)
async def get_device_chat_history(
    device_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Get chat history from file storage"""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    messages = file_storage.load_chat_history(device_id)
    total = len(messages)
    messages = messages[-max(limit, 1):]

    return DeviceChatHistoryResponse(
        device_id=device_id,
        task_id=None,
        messages=[_build_chat_message_response(message) for message in messages],
        total=total,
    )


@router.post("/devices/{device_id}/chat", response_model=ApiResponse)
async def add_chat_message(
    device_id: str,
    payload: dict,
    db: Session = Depends(get_db),
):
    """Add a chat message to file storage"""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    message = {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "role": payload.get("role", "agent"),
        "content": payload.get("content", ""),
        "thinking": payload.get("thinking"),
        "action_type": payload.get("action_type"),
        "action_params": payload.get("action_params"),
        "screenshot_path": payload.get("screenshot_path"),
        "created_at": datetime.now().isoformat(),
        "task_id": payload.get("task_id"),
        "step_number": payload.get("step_number"),
        "phase": payload.get("phase"),
        "stage": payload.get("stage"),
        "progress_status_text": payload.get("progress_status_text"),
        "progress_message": payload.get("progress_message", payload.get("message")),
        "result": payload.get("result"),
        "success": payload.get("success"),
        "error": payload.get("error"),
        "error_type": payload.get("error_type"),
        "version": payload.get("version"),
        "error_code": payload.get("error_code"),
        "data": payload.get("data"),
    }

    file_storage.append_chat_message(device_id, message)

    return ApiResponse(
        success=True,
        message="Chat message stored",
        data={"id": message["id"], "created_at": message["created_at"]},
    )


@router.delete("/devices/{device_id}/session-context", response_model=ApiResponse)
async def clear_session_context_endpoint(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Clear session context (memory + file) for a device. Used by user-initiated 'clear context' button."""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    scheduler.clear_session_context(device_id)
    return ApiResponse(success=True, message="Session context cleared")


@router.delete("/devices/{device_id}/chat", response_model=ApiResponse)
async def clear_device_chat_history(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Clear chat history from file storage"""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    file_storage.save_chat_history(device_id, [])

    return ApiResponse(success=True, message="Chat history cleared")


@router.get("/devices/{device_id}/history")
async def get_device_history(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Get ReAct records and react history for a device"""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    react_records = file_storage.get_react_records(device_id)
    chat_history = file_storage.load_chat_history(device_id)
    screenshots = file_storage.get_screenshots(device_id)

    return {
        "device_id": device_id,
        "react_records": react_records,
        "chat_history": chat_history,
        "screenshots": screenshots,
    }


@router.get("/devices/{device_id}/artifacts")
async def get_device_artifacts(
    device_id: str,
    db: Session = Depends(get_db),
):
    """List archived artifact files for a device."""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    latest_screenshot = file_storage.get_latest_screenshot_path(device_id)
    latest_log = file_storage.get_log_file_path(device_id)
    react_records_file = file_storage.get_react_records_file_path(device_id)
    chat_history_file = file_storage.get_chat_history_file_path(device_id)

    return {
        "device_id": device_id,
        "screenshots": file_storage.get_screenshots(device_id),
        "latest_screenshot": latest_screenshot.name if latest_screenshot else None,
        "latest_screenshot_download": f"/api/v1/devices/{device_id}/artifacts/screenshot/latest" if latest_screenshot else None,
        "latest_log_download": f"/api/v1/devices/{device_id}/artifacts/logs/latest" if latest_log else None,
        "react_records_download": f"/api/v1/devices/{device_id}/artifacts/react-records" if react_records_file else None,
        "chat_history_download": f"/api/v1/devices/{device_id}/artifacts/chat-history" if chat_history_file else None,
    }


@router.get("/devices/{device_id}/artifacts/file")
async def download_device_artifact_file(
    device_id: str,
    path: str = Query(..., description="Relative device artifact path, e.g. screenshots/step_1_xxx.png"),
    db: Session = Depends(get_db),
):
    """Download a device artifact file by relative path."""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    artifact_path = file_storage.get_device_file(device_id, path)
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(artifact_path, filename=artifact_path.name)


@router.get("/devices/{device_id}/artifacts/screenshot/latest")
async def download_latest_screenshot(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Download the latest screenshot for a device."""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    path = file_storage.get_latest_screenshot_path(device_id)
    if not path:
        raise HTTPException(status_code=404, detail="Latest screenshot not found")
    return FileResponse(path, filename=path.name)


@router.get("/devices/{device_id}/artifacts/logs/latest")
async def download_latest_log(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Download the latest adb log jsonl file for a device."""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    path = file_storage.get_log_file_path(device_id)
    if not path:
        raise HTTPException(status_code=404, detail="Latest log not found")
    return FileResponse(path, filename=path.name, media_type="application/json")


@router.get("/devices/{device_id}/artifacts/react-records")
async def download_react_records(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Download react_records.jsonl for a device."""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    path = file_storage.get_react_records_file_path(device_id)
    if not path:
        raise HTTPException(status_code=404, detail="react_records.jsonl not found")
    return FileResponse(path, filename=path.name, media_type="application/json")


@router.get("/devices/{device_id}/artifacts/chat-history")
async def download_chat_history(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Download chat_history.json for a device."""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    path = file_storage.get_chat_history_file_path(device_id)
    if not path:
        raise HTTPException(status_code=404, detail="chat_history.json not found")
    return FileResponse(path, filename=path.name, media_type="application/json")


@router.post("/devices/{device_id}/interrupt", response_model=ApiResponse)
async def interrupt_device_task(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Interrupt the active task on a device"""
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    active_task = scheduler.get_task(device_id)
    task_id = active_task.task_id if active_task else None

    await scheduler.interrupt_task(device_id)
    await device_status_manager.set_idle(device_id)

    api_logger.info(f"[interrupt_device] Task interrupted - device_id={device_id}, task_id={task_id}")

    return ApiResponse(
        success=True,
        message=f"Task on device {device_id} interrupted",
        data={"device_id": device_id, "task_id": task_id},
    )
