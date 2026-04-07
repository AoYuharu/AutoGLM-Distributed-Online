"""
Device API routes - Simplified

Only contains POST /status endpoint for receiving device status updates from clients.
"""
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database import get_db
from src.models.models import Device, Client
from src.schemas.schemas import (
    ApiResponse,
    DeviceStatusMessage,
    DeviceResponse,
    DeviceListResponse,
)
from src.logging_config import get_api_logger, get_network_logger
from src.services.device_status_manager import device_status_manager, DeviceStatus
import structlog

# 使用模块化日志器
api_logger = get_api_logger()
network_logger = get_network_logger()
logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


# === HTTP-based Device Status Endpoint ===

@router.post("/status", response_model=ApiResponse)
async def update_device_status_http(
    message: DeviceStatusMessage,
    db: Session = Depends(get_db),
):
    """
    Receive device status from client via HTTP POST

    This endpoint receives device status updates from clients that were
    previously sent via WebSocket. This is used for:
    - device_status: Periodic status updates

    The message is sent as JSON with the following structure:
    {
        "msg_id": "uuid-v4",
        "type": "device_status",
        "version": "1.0",
        "timestamp": "2026-04-02T10:00:00.000Z",
        "client_id": "client_001",
        "payload": {
            "devices": [...]
        }
    }
    """
    result = await handle_device_status_http(message.model_dump(), db)
    return ApiResponse(**result)


async def handle_device_status_http(message: dict, db: Session) -> dict:
    """
    Handle device_status message from HTTP endpoint.

    Args:
        message: The message dict containing device_status info
        db: Database session

    Returns:
        Response dict
    """
    api_logger.info("[device_status_http] === FUNCTION CALLED ===")
    client_id = message.get("client_id")
    payload = message.get("payload", {})
    devices = payload.get("devices", [])

    # 网络消息归档日志
    network_logger.info(f"[device_status] Received from client_id={client_id}, devices={len(devices)}")

    if not devices:
        return {"success": False, "message": "No devices in payload"}

    # Find or create client
    db_client = None
    if client_id:
        db_client = db.execute(
            select(Client).where(Client.client_id == client_id)
        ).scalar_one_or_none()

    if not db_client:
        # Create default client if not exists
        import uuid
        db_client = Client(
            client_id=client_id or "default",
            name=f"Client {client_id[:8]}" if client_id else "Default Client",
            api_key=str(uuid.uuid4()),
        )
        db.add(db_client)
        db.flush()

    updated_devices = []
    for device_info in devices:
        status_device_id = device_info.get("device_id")
        status = device_info.get("status", "idle")
        platform = device_info.get("platform", "android")
        device_name = device_info.get("device_name")
        os_version = device_info.get("os_version")
        screen_size = device_info.get("screen_size")
        capabilities = device_info.get("capabilities")
        current_task_id = device_info.get("current_task_id")

        api_logger.info(f"[device_status_http] Processing device: {status_device_id}")

        # Find device in database
        device = db.execute(
            select(Device).where(Device.device_id == status_device_id)
        ).scalar_one_or_none()

        if device:
            # Update existing device (only static info, not status which is in memory)
            device.last_heartbeat = datetime.utcnow()

            if device_name:
                device.model = device_name
            if os_version:
                device.os_version = os_version
            if screen_size and len(screen_size) == 2:
                device.screen_width = screen_size[0]
                device.screen_height = screen_size[1]
            if capabilities:
                device.capabilities = capabilities

            api_logger.info(f"[device_status_http] Device updated (static info only) - {status_device_id}")
        else:
            # Create new device (status field still set for DB compatibility but not used for dynamic state)
            new_device = Device(
                device_id=status_device_id,
                client_id=db_client.id,
                platform=platform,
                model=device_name,
                os_version=os_version,
                screen_width=screen_size[0] if screen_size and len(screen_size) == 2 else None,
                screen_height=screen_size[1] if screen_size and len(screen_size) == 2 else None,
                capabilities=capabilities,
                status="idle",  # Static default, actual status is in memory
            )
            db.add(new_device)

            api_logger.info(f"[device_status_http] Device registered - {status_device_id}, platform={platform}")

        updated_devices.append(status_device_id)

        # Bind real device_id to existing client WebSocket connection when available
        from src.services.websocket import ws_hub
        connection_id = ws_hub._client_connections.get(client_id) if client_id else None
        if connection_id:
            ws_hub.register_device(connection_id, status_device_id, client_id, capabilities or {})

        # Update DeviceStatusManagerTable
        api_logger.info(f"[device_status_http] Setting device {status_device_id} status to {status}")
        memory_entry = await device_status_manager.get_entry(status_device_id)
        from src.services.react_scheduler import scheduler
        active_task = scheduler.get_task(status_device_id)
        active_task_id = active_task.task_id if active_task and active_task.is_active else None
        preserved_task_id = current_task_id or active_task_id or (memory_entry.current_task_id if memory_entry else None)

        if status == "offline":
            await device_status_manager.set_offline(status_device_id)
        elif status == "busy":
            await device_status_manager.set_busy(status_device_id, preserved_task_id)
        elif preserved_task_id:
            api_logger.info(
                f"[device_status_http] Preserving busy status for active task: {status_device_id}, task={preserved_task_id}"
            )
            await device_status_manager.set_busy(status_device_id, preserved_task_id)
        else:
            await device_status_manager.set_idle(status_device_id)
        api_logger.info(f"[device_status_http] Device {status_device_id} status set complete")

    db.commit()

    # Broadcast current device status to Web Consoles
    # Note: We don't call sync_all_devices here because:
    # 1. HTTP-reported devices (via device_id) are different from WebSocket-connected clients (via client_id)
    # 2. The device status is already updated via set_idle/set_busy/set_offline above
    from src.services.websocket import ws_hub
    all_entries = await device_status_manager.get_all_devices()
    sync_devices = [
        {
            "device_id": device_id,
            "status": entry.status.value,
            "last_update": entry.last_update.isoformat(),
        }
        for device_id, entry in all_entries.items()
    ]
    await ws_hub.broadcast_device_sync(sync_devices)
    api_logger.info(f"[device_status_http] Broadcasting all devices status")

    return {
        "success": True,
        "message": f"Updated {len(updated_devices)} devices",
        "devices": updated_devices,
    }


# === HTTP-based Device Offline Endpoint ===

class DeviceOfflineMessage(BaseModel):
    """Device offline message schema"""
    type: str = "device_offline"
    version: str = "1.0"
    payload: dict


@router.post("/offline", response_model=ApiResponse)
async def report_device_offline(
    message: DeviceOfflineMessage,
    db: Session = Depends(get_db),
):
    """
    接收 Client 报告的设备离线

    当 Client 检测到设备断开连接时，调用此接口将设备标记为离线
    """
    device_id = message.payload.get("device_id")
    if not device_id:
        return ApiResponse(success=False, message="device_id is required")

    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if device:
        # Note: status is managed in memory via device_status_manager, not DB
        api_logger.info(f"[device_offline] Device marked offline: {device_id}")

        # Update DeviceStatusManager (single source of truth)
        await device_status_manager.set_offline(device_id)

    return ApiResponse(success=True, message=f"Device {device_id} marked offline")


# === HTTP-based Device List Endpoint ===

@router.get("", response_model=DeviceListResponse)
async def list_devices(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Get all devices from database.

    Query parameters:
    - platform: Filter by platform (android, harmonyos, ios)
    - status: Filter by status (idle, busy, offline, error)
    """
    query = select(Device)

    if platform:
        query = query.where(Device.platform == platform)
    # Note: status filter is applied after getting memory status, not from DB

    devices = db.execute(query).scalars().all()

    # Convert to response format
    device_list = []
    online_count = 0
    offline_count = 0

    for device in devices:
        # Get status from memory (DeviceStatusManager), not from DB
        memory_entry = await device_status_manager.get_entry(device.device_id)
        memory_status = memory_entry.status.value if memory_entry else "offline"
        memory_task_id = memory_entry.current_task_id if memory_entry else None

        # Apply status filter from memory, not DB
        if status and memory_status != status:
            continue

        device_response = DeviceResponse(
            id=device.id,
            device_id=device.device_id,
            client_id=device.client_id,
            platform=device.platform,
            device_name=device.model,  # Map DB 'model' to API 'device_name'
            model=device.model,
            os_version=device.os_version,
            screen_width=device.screen_width,
            screen_height=device.screen_height,
            status=memory_status,
            last_seen=device.last_heartbeat,
            current_task_id=memory_task_id,
            remark=device.remark,
        )
        device_list.append(device_response)

        if memory_status == "offline":
            offline_count += 1
        else:
            online_count += 1

    return DeviceListResponse(
        devices=device_list,
        total=len(device_list),
        online=online_count,
        offline=offline_count,
    )


# === HTTP-based Device Delete Endpoint ===

@router.delete("/{device_id}", response_model=ApiResponse)
async def delete_device(
    device_id: str,
    db: Session = Depends(get_db),
):
    """
    删除设备

    从数据库和设备状态管理器中删除设备
    """
    device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
    if not device:
        return ApiResponse(success=False, message=f"Device {device_id} not found")

    db.delete(device)
    db.commit()

    # 从 DeviceStatusManager 中移除
    await device_status_manager.remove_device(device_id)

    # 从 WebSocket hub 中断开设备连接
    from src.services.websocket import ws_hub
    ws_hub.unregister_device(device_id)

    api_logger.info(f"[delete_device] Device deleted: {device_id}")

    return ApiResponse(success=True, message=f"Device {device_id} deleted")
