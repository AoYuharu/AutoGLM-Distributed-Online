"""
Task API routes - Simplified

Contains:
- POST /observe: Receive observe results from clients
- GET /tasks: List all tasks
- POST /tasks: Create a new task
- GET /tasks/{task_id}: Get task details
- POST /tasks/{task_id}/interrupt: Interrupt a task
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database import get_db
from src.models.models import Task, Device, Client, TaskStep, ChatMessage
from src.schemas.schemas import (
    ApiResponse,
    DeviceChatHistoryResponse,
    DeviceTaskSessionResponse,
    ObserveResultMessage,
    TaskCreate,
    TaskResponse,
    TaskDetailResponse,
    TaskListResponse,
    TaskStepResponse,
)
from src.logging_config import get_api_logger, get_network_logger
from src.services.react_scheduler import scheduler
from src.services.device_status_manager import device_status_manager
import structlog

api_logger = get_api_logger()
network_logger = get_network_logger()
logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


def _get_latest_screenshot(task: Optional[Task], steps: list[TaskStep]) -> Optional[str]:
    for step in reversed(steps):
        if step.screenshot_url:
            return step.screenshot_url

    if task and isinstance(task.result, dict):
        final_screenshot = task.result.get("final_screenshot")
        if isinstance(final_screenshot, str) and final_screenshot:
            return final_screenshot

    return None


def _build_synthesized_chat_history(task: Optional[Task], steps: list[TaskStep]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    if not task:
        return messages

    messages.append(
        ChatMessage(
            id=f"{task.task_id}_user",
            device_id=task.device_id,
            role="user",
            content=task.instruction,
            created_at=task.created_at,
        )
    )

    for step in steps:
        if step.error:
            content = f"步骤 {step.step_number} 失败: {step.error}"
        else:
            action_type = step.action_type or "observe"
            params = step.action_params or {}
            content = (
                f"步骤 {step.step_number}: {action_type} - {params}"
                if params else f"步骤 {step.step_number}: {action_type}"
            )

        messages.append(
            ChatMessage(
                id=step.id,
                device_id=task.device_id,
                role="agent",
                content=content,
                thinking=step.thinking,
                action_type=step.action_type,
                action_params=step.action_params,
                screenshot_path=step.screenshot_url,
                created_at=step.created_at,
            )
        )

    final_message = None
    if task.status == "completed":
        result = task.result if isinstance(task.result, dict) else {}
        final_message = result.get("finish_message") or result.get("message") or "任务已完成"
    elif task.status == "failed":
        result = task.result if isinstance(task.result, dict) else {}
        final_message = result.get("error") or result.get("message") or task.error_message or "任务执行失败"
    elif task.status == "interrupted":
        result = task.result if isinstance(task.result, dict) else {}
        final_message = result.get("message") or task.error_message or "任务已中断"

    if final_message:
        messages.append(
            ChatMessage(
                id=f"{task.task_id}_final_{task.status}",
                device_id=task.device_id,
                role="agent",
                content=final_message,
                screenshot_path=_get_latest_screenshot(task, steps),
                created_at=task.finished_at or task.started_at or task.created_at,
            )
        )

    return messages


def _resolve_task_for_device(db: Session, device: Device) -> Optional[Task]:
    active_scheduler_task = scheduler.get_task(device.device_id)
    active_task_id = (
        active_scheduler_task.task_id if active_scheduler_task and active_scheduler_task.is_active else None
    )

    if active_task_id:
        task = db.execute(select(Task).where(Task.task_id == active_task_id)).scalar_one_or_none()
        if task:
            return task

    return db.execute(
        select(Task)
        .where(Task.device_id == device.id)
        .order_by(Task.created_at.desc())
    ).scalars().first()


def _get_task_steps(db: Session, task: Optional[Task]) -> list[TaskStep]:
    if task is None:
        return []

    return db.execute(
        select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.step_number)
    ).scalars().all()


def _build_chat_message_response(message: ChatMessage):
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "thinking": message.thinking,
        "action_type": message.action_type,
        "action_params": message.action_params,
        "screenshot_path": message.screenshot_path,
        "created_at": message.created_at,
    }


def _upsert_task_step_from_observe(
    db: Session,
    task_row: Task,
    *,
    step_number: int,
    screenshot: Optional[str],
    success: bool,
    error: Optional[str],
):
    if step_number <= 0:
        return

    step = db.execute(
        select(TaskStep).where(
            TaskStep.task_id == task_row.id,
            TaskStep.step_number == step_number,
        )
    ).scalar_one_or_none()

    if step is None:
        step = TaskStep(
            task_id=task_row.id,
            device_id=task_row.device_id,
            step_number=step_number,
            action_type="",
            action_params={},
            thinking="",
            success=success,
            error=error,
            screenshot_url=screenshot,
        )
        db.add(step)
        return

    step.success = success
    step.error = error
    step.screenshot_url = screenshot


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

    task_row = db.execute(select(Task).where(Task.task_id == task_id)).scalar_one_or_none()
    if task_row:
        if task_row.started_at is None:
            task_row.started_at = datetime.utcnow()

        _upsert_task_step_from_observe(
            db,
            task_row,
            step_number=step_number or 0,
            screenshot=screenshot,
            success=success,
            error=error,
        )

        if (step_number or 0) > 0:
            task_row.current_step = max(task_row.current_step or 0, step_number)

        if success:
            if task_row.status not in {"completed", "failed", "interrupted"}:
                task_row.status = "running"
        else:
            task_row.status = "failed"
            task_row.error_message = error or result or "observe_error"
            task_row.finished_at = datetime.utcnow()
            task_row.result = {
                "success": False,
                "error": error,
                "result": result,
                "version": round_version,
                "step_number": step_number,
            }

    handled = False
    if action_router and round_version is not None and device_id:
        handled = await action_router.handle_observe_result(
            {
                **payload,
                "version": round_version,
            }
        )

    if screenshot and device_id:
        await scheduler.set_observe_result(device_id, screenshot, result or "")
        scheduler.requeue_task(device_id)
        handled = True

    db.commit()

    return {
        "success": True,
        "message": "Observe result received" if handled else "Observe result recorded",
    }


@router.post("", response_model=TaskResponse)
async def create_task(
    task_data: TaskCreate,
    db: Session = Depends(get_db),
):
    device_id = task_data.device_id
    platform = task_data.platform

    device = None
    if device_id:
        device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    elif platform:
        devices_with_platform = db.execute(
            select(Device).where(Device.platform == platform)
        ).scalars().all()
        for d in devices_with_platform:
            if await device_status_manager.is_device_ok(d.device_id):
                device = d
                break

    if not device:
        raise HTTPException(status_code=404, detail="No available device found")

    client = db.execute(select(Client).where(Client.id == device.client_id)).scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    task_id = f"task_{uuid.uuid4().hex[:12]}"
    acquired = await device_status_manager.try_acquire_task(device.device_id, task_id)
    if not acquired:
        memory_entry = await device_status_manager.get_entry(device.device_id)
        current_status = memory_entry.status.value if memory_entry else "unknown"
        raise HTTPException(
            status_code=400,
            detail=f"Device {device.device_id} is not available (status: {current_status})",
        )

    try:
        new_task = Task(
            task_id=task_id,
            device_id=device.id,
            client_id=client.id,
            instruction=task_data.instruction,
            mode=task_data.mode,
            max_steps=task_data.max_steps,
            priority=task_data.priority,
            status="pending",
        )
        db.add(new_task)
        db.flush()

        scheduler.submit_task(
            device_id=device.device_id,
            task_id=task_id,
            instruction=task_data.instruction,
            mode=task_data.mode,
            max_steps=task_data.max_steps,
        )

        db.commit()
        db.refresh(new_task)
    except Exception:
        await device_status_manager.set_idle(device.device_id)
        db.rollback()
        raise

    api_logger.info(f"[create_task] Task created - task_id={task_id}, device_id={device.device_id}")

    return TaskResponse(
        id=new_task.id,
        task_id=new_task.task_id,
        device_id=device.device_id,
        instruction=new_task.instruction,
        status=new_task.status,
        mode=new_task.mode,
        max_steps=new_task.max_steps,
        current_step=new_task.current_step,
        created_at=new_task.created_at,
        started_at=new_task.started_at,
        finished_at=new_task.finished_at,
        result=new_task.result,
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    status: Optional[str] = None,
    device_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = select(Task)

    if status:
        query = query.where(Task.status == status)

    if device_id:
        device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
        if device:
            query = query.where(Task.device_id == device.id)
        else:
            return TaskListResponse(tasks=[], total=0)

    query = query.order_by(Task.created_at.desc())
    total = len(db.execute(query).scalars().all())
    query = query.limit(limit).offset(offset)
    tasks = db.execute(query).scalars().all()

    task_list = []
    for task in tasks:
        device = db.execute(select(Device).where(Device.id == task.device_id)).scalar_one_or_none()
        device_id_str = device.device_id if device else ""

        task_list.append(
            TaskResponse(
                id=task.id,
                task_id=task.task_id,
                device_id=device_id_str,
                instruction=task.instruction,
                status=task.status,
                mode=task.mode,
                max_steps=task.max_steps,
                current_step=task.current_step,
                created_at=task.created_at,
                started_at=task.started_at,
                finished_at=task.finished_at,
                result=task.result,
            )
        )

    return TaskListResponse(tasks=task_list, total=total)


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(
    task_id: str,
    db: Session = Depends(get_db),
):
    task = db.execute(select(Task).where(Task.task_id == task_id)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    device = db.execute(select(Device).where(Device.id == task.device_id)).scalar_one_or_none()
    device_id_str = device.device_id if device else ""

    steps = db.execute(
        select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.step_number)
    ).scalars().all()

    from src.schemas.schemas import TaskStepResponse

    step_responses = [
        TaskStepResponse(
            id=step.id,
            step_number=step.step_number,
            action_type=step.action_type,
            action_params=step.action_params,
            thinking=step.thinking,
            duration_ms=step.duration_ms,
            success=step.success,
            error=step.error,
            screenshot_url=step.screenshot_url,
            created_at=step.created_at,
        )
        for step in steps
    ]

    return TaskDetailResponse(
        id=task.id,
        task_id=task.task_id,
        device_id=device_id_str,
        instruction=task.instruction,
        status=task.status,
        mode=task.mode,
        max_steps=task.max_steps,
        current_step=task.current_step,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        result=task.result,
        steps=step_responses,
    )


@router.get("/devices/{device_id}/session", response_model=DeviceTaskSessionResponse)
async def get_device_task_session(
    device_id: str,
    db: Session = Depends(get_db),
):
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    task = _resolve_task_for_device(db, device)
    steps = _get_task_steps(db, task)

    persisted_messages = db.execute(
        select(ChatMessage)
        .where(ChatMessage.device_id == device.id)
        .order_by(ChatMessage.created_at.asc())
    ).scalars().all()

    chat_history = persisted_messages or _build_synthesized_chat_history(task, steps)
    active_scheduler_task = scheduler.get_task(device.device_id)
    has_active_task = bool(active_scheduler_task and active_scheduler_task.is_active)

    latest_error_reason = None
    if task:
        if isinstance(task.result, dict):
            latest_error_reason = task.result.get("error") or task.result.get("message")
        latest_error_reason = latest_error_reason or task.error_message

    return DeviceTaskSessionResponse(
        device_id=device.device_id,
        task_id=task.task_id if task else None,
        status=task.status if task else None,
        instruction=task.instruction if task else None,
        current_step=task.current_step if task else 0,
        max_steps=task.max_steps if task else 0,
        latest_screenshot=_get_latest_screenshot(task, steps),
        interruptible=has_active_task,
        latest_error_reason=latest_error_reason,
        chat_history=[_build_chat_message_response(message) for message in chat_history],
    )


@router.get("/devices/{device_id}/chat", response_model=DeviceChatHistoryResponse)
async def get_device_chat_history(
    device_id: str,
    limit: int = 50,
    task_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    task = None
    if task_id:
        task = db.execute(select(Task).where(Task.task_id == task_id)).scalar_one_or_none()
    else:
        task = _resolve_task_for_device(db, device)

    persisted_messages = db.execute(
        select(ChatMessage)
        .where(ChatMessage.device_id == device.id)
        .order_by(ChatMessage.created_at.asc())
    ).scalars().all()

    if persisted_messages:
        messages = persisted_messages[-max(limit, 1):]
        total = len(persisted_messages)
    else:
        synthesized_messages = _build_synthesized_chat_history(task, _get_task_steps(db, task))
        total = len(synthesized_messages)
        messages = synthesized_messages[-max(limit, 1):]

    return DeviceChatHistoryResponse(
        device_id=device.device_id,
        task_id=task.task_id if task else None,
        messages=[_build_chat_message_response(message) for message in messages],
        total=total,
    )


@router.post("/{task_id}/interrupt", response_model=ApiResponse)
async def interrupt_task(
    task_id: str,
    db: Session = Depends(get_db),
):
    task = db.execute(select(Task).where(Task.task_id == task_id)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    device = db.execute(select(Device).where(Device.id == task.device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    task.status = "interrupted"
    task.finished_at = datetime.utcnow()
    task.error_message = task.error_message or "user_interrupted"
    db.commit()

    api_logger.info(f"[interrupt_task] Task interrupted - task_id={task_id}")
    await scheduler.interrupt_task(device.device_id)

    return ApiResponse(
        success=True,
        message=f"Task {task_id} interrupted",
    )


@router.post("/devices/{device_id}/chat", response_model=ApiResponse)
async def add_chat_message(
    device_id: str,
    payload: dict,
    db: Session = Depends(get_db),
):
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    message = ChatMessage(
        device_id=device.id,
        role=payload.get("role", "agent"),
        content=payload.get("content", ""),
        thinking=payload.get("thinking"),
        action_type=payload.get("action_type"),
        action_params=payload.get("action_params"),
        screenshot_path=payload.get("screenshot_path"),
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    return ApiResponse(
        success=True,
        message="Chat message stored",
        data={"id": message.id, "created_at": message.created_at},
    )


@router.delete("/devices/{device_id}/chat", response_model=ApiResponse)
async def clear_device_chat_history(
    device_id: str,
    db: Session = Depends(get_db),
):
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    messages = db.execute(select(ChatMessage).where(ChatMessage.device_id == device.id)).scalars().all()
    for message in messages:
        db.delete(message)
    db.commit()

    return ApiResponse(success=True, message="Chat history cleared")

