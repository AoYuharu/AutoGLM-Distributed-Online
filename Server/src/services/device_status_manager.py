"""
Device Status Manager Table - Server-side device state management

Maintains an in-memory table of device states for the ReAct workflow.
Thread-safe with asyncio.Lock for concurrent access.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional
import asyncio
import uuid

import structlog

logger = structlog.get_logger()


class DeviceStatus(str, Enum):
    """Device status enum"""

    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass
class DeviceStatusEntry:
    """Entry in the device status table"""

    device_id: str
    status: DeviceStatus = DeviceStatus.OFFLINE
    last_update: datetime = field(default_factory=datetime.utcnow)
    current_task_id: Optional[str] = None  # deprecated alias of current_session_id

    # Session + Run identity (replaces current_task_id as primary model)
    current_session_id: Optional[str] = None  # persistent session identifier
    current_run_id: Optional[str] = None     # per-auto-run identifier
    session_started_at: Optional[datetime] = None
    run_started_at: Optional[datetime] = None

    version_code: int = 0

    @property
    def effective_session_id(self) -> Optional[str]:
        """Primary session identifier (session_id takes precedence over deprecated task_id)."""
        return self.current_session_id or self.current_task_id

    @property
    def effective_task_id(self) -> Optional[str]:
        """Deprecated alias. Prefer effective_session_id."""
        return self.effective_session_id


class DeviceStatusManager:
    """Server-side device state management table."""

    OFFLINE_CHECK_INTERVAL = 10 * 60
    STALE_THRESHOLD = timedelta(minutes=10)

    def __init__(self):
        self._devices: Dict[str, DeviceStatusEntry] = {}
        self._lock = asyncio.Lock()
        self._offline_checker_task: Optional[asyncio.Task] = None
        self._started = False
        logger.info("DeviceStatusManager initialized")

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._offline_checker_task = asyncio.create_task(self._offline_check_loop())
        logger.info("Offline checker daemon started")

    async def update_status(
        self,
        device_id: str,
        status: DeviceStatus,
        task_id: Optional[str] = None,
        *,
        clear_task: bool = False,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        session_started_at: Optional[datetime] = None,
        run_started_at: Optional[datetime] = None,
    ):
        async with self._lock:
            if device_id not in self._devices:
                self._devices[device_id] = DeviceStatusEntry(device_id=device_id)
            entry = self._devices[device_id]
            entry.status = status
            entry.last_update = datetime.utcnow()
            if clear_task:
                entry.current_task_id = None
                entry.current_session_id = None
                entry.current_run_id = None
                entry.session_started_at = None
                entry.run_started_at = None
            else:
                if task_id is not None:
                    entry.current_task_id = task_id
                if session_id is not None:
                    entry.current_session_id = session_id
                if run_id is not None:
                    entry.current_run_id = run_id
                if session_started_at is not None:
                    entry.session_started_at = session_started_at
                if run_started_at is not None:
                    entry.run_started_at = run_started_at

            logger.debug(
                "Device status updated",
                device_id=device_id,
                status=status.value,
                task_id=entry.current_task_id,
            )

    async def touch(self, device_id: str):
        async with self._lock:
            if device_id not in self._devices:
                self._devices[device_id] = DeviceStatusEntry(device_id=device_id)
            self._devices[device_id].last_update = datetime.utcnow()

    async def get_status(self, device_id: str) -> DeviceStatus:
        async with self._lock:
            if device_id not in self._devices:
                return DeviceStatus.OFFLINE
            return self._devices[device_id].status

    async def get_entry(self, device_id: str) -> Optional[DeviceStatusEntry]:
        async with self._lock:
            return self._devices.get(device_id)

    async def increment_version(self, device_id: str) -> int:
        async with self._lock:
            if device_id not in self._devices:
                self._devices[device_id] = DeviceStatusEntry(device_id=device_id)
            self._devices[device_id].version_code += 1
            self._devices[device_id].last_update = datetime.utcnow()
            return self._devices[device_id].version_code

    async def get_version(self, device_id: str) -> int:
        async with self._lock:
            if device_id not in self._devices:
                return 0
            return self._devices[device_id].version_code

    async def set_busy(self, device_id: str, task_id: str, session_id: Optional[str] = None, run_id: Optional[str] = None):
        await self.update_status(device_id, DeviceStatus.BUSY, task_id, session_id=session_id, run_id=run_id, session_started_at=datetime.utcnow(), run_started_at=datetime.utcnow())
        logger.info("Device set busy", device_id=device_id, task_id=task_id, session_id=session_id, run_id=run_id)

    async def try_acquire_task(self, device_id: str, task_id: str, session_id: Optional[str] = None, run_id: Optional[str] = None) -> bool:
        async with self._lock:
            entry = self._devices.get(device_id)
            if entry is None:
                entry = DeviceStatusEntry(device_id=device_id, status=DeviceStatus.OFFLINE)
                self._devices[device_id] = entry
                logger.info(f"[try_acquire] Created new entry with OFFLINE for {device_id}")

            if entry.status != DeviceStatus.IDLE or entry.current_task_id:
                logger.info(f"[try_acquire] Failed for {device_id}: status={entry.status}, current_task_id={entry.current_task_id}")
                return False

            entry.status = DeviceStatus.BUSY
            entry.current_task_id = task_id
            entry.current_session_id = session_id or task_id
            entry.current_run_id = run_id
            entry.session_started_at = datetime.utcnow()
            entry.run_started_at = datetime.utcnow()
            entry.last_update = datetime.utcnow()
            logger.info("Device acquired for task", device_id=device_id, task_id=task_id, session_id=session_id, run_id=run_id)
            return True

    async def set_idle(self, device_id: str):
        await self.update_status(device_id, DeviceStatus.IDLE, clear_task=True)
        logger.info("Device set idle", device_id=device_id)

    async def set_offline(self, device_id: str):
        await self.update_status(device_id, DeviceStatus.OFFLINE, clear_task=True)
        logger.info("Device set offline", device_id=device_id)

    async def is_device_ok(self, device_id: str) -> bool:
        status = await self.get_status(device_id)
        return status == DeviceStatus.IDLE

    async def is_device_offline(self, device_id: str) -> bool:
        status = await self.get_status(device_id)
        return status == DeviceStatus.OFFLINE

    async def is_device_busy(self, device_id: str) -> bool:
        status = await self.get_status(device_id)
        return status == DeviceStatus.BUSY

    async def get_all_devices(self) -> Dict[str, DeviceStatusEntry]:
        async with self._lock:
            return self._devices.copy()

    async def get_device_count(self) -> int:
        async with self._lock:
            return len(self._devices)

    async def remove_device(self, device_id: str):
        async with self._lock:
            if device_id in self._devices:
                del self._devices[device_id]
                logger.info("Device removed from table", device_id=device_id)

    async def sync_all_devices(self, connected_device_ids: list) -> dict:
        changed: list = []

        scheduler = None
        try:
            from src.services.react_scheduler import scheduler as scheduler_instance

            scheduler = scheduler_instance
        except Exception:
            scheduler = None

        async with self._lock:
            connected_set = set(connected_device_ids)

            for device_id in list(self._devices.keys()):
                entry = self._devices[device_id]
                if device_id not in connected_set and entry.status != DeviceStatus.OFFLINE:
                    entry.status = DeviceStatus.OFFLINE
                    entry.current_task_id = None
                    entry.last_update = datetime.utcnow()
                    changed.append(device_id)
                    logger.info("Device marked offline (sync)", device_id=device_id)

            for device_id in connected_device_ids:
                if device_id not in self._devices:
                    self._devices[device_id] = DeviceStatusEntry(
                        device_id=device_id,
                        status=DeviceStatus.IDLE,
                    )
                    changed.append(device_id)
                    logger.info("Device added as idle (sync)", device_id=device_id)
                    continue

                entry = self._devices[device_id]
                entry.last_update = datetime.utcnow()
                active_task = scheduler.get_task(device_id) if scheduler else None
                active_task_id = active_task.task_id if active_task and active_task.is_active else None
                if active_task_id:
                    if entry.current_task_id != active_task_id:
                        entry.current_task_id = active_task_id
                    desired_status = DeviceStatus.BUSY
                else:
                    if entry.current_task_id is not None:
                        entry.current_task_id = None
                    desired_status = DeviceStatus.IDLE
                if entry.status != desired_status:
                    entry.status = desired_status
                    changed.append(device_id)
                    logger.info(
                        "Device status restored (sync)",
                        device_id=device_id,
                        restored_status=entry.status.value,
                    )

            return {
                "changed": changed,
                "devices": {
                    k: {
                        "status": v.status.value,
                        "last_update": v.last_update.isoformat(),
                        "current_task_id": v.current_task_id,
                    }
                    for k, v in self._devices.items()
                },
            }

    async def _offline_check_loop(self) -> None:
        while True:
            await asyncio.sleep(self.OFFLINE_CHECK_INTERVAL)
            try:
                stale_devices = await self._mark_stale_devices_offline()
                if stale_devices:
                    logger.info(
                        f"Marked {len(stale_devices)} stale devices as offline: {stale_devices}"
                    )
                    await self._broadcast_offline_devices(stale_devices)
            except Exception as e:
                logger.error(f"Error in offline check loop: {e}")

    async def _broadcast_offline_devices(self, stale_device_ids: List[str]) -> None:
        try:
            from src.services.websocket import ws_hub

            all_entries = await self.get_all_devices()
            sync_devices = [
                {
                    "device_id": device_id,
                    "status": entry.status.value,
                    "last_update": entry.last_update.isoformat(),
                }
                for device_id, entry in all_entries.items()
                if device_id in stale_device_ids
            ]
            if sync_devices:
                await ws_hub.broadcast_device_sync(sync_devices)
                logger.info(f"Broadcast offline devices to Web Console: {stale_device_ids}")
        except Exception as e:
            logger.error(f"Failed to broadcast offline devices: {e}")

    async def _mark_stale_devices_offline(self) -> List[str]:
        stale_devices: List[str] = []
        now = datetime.utcnow()

        async with self._lock:
            for device_id, entry in self._devices.items():
                if entry.status != DeviceStatus.OFFLINE:
                    time_since_update = now - entry.last_update
                    if time_since_update > self.STALE_THRESHOLD:
                        entry.status = DeviceStatus.OFFLINE
                        entry.current_task_id = None
                        entry.last_update = now
                        stale_devices.append(device_id)
                        logger.info(
                            "Device marked offline (stale)",
                            device_id=device_id,
                            last_update=entry.last_update.isoformat(),
                            stale_minutes=int(time_since_update.total_seconds() / 60),
                        )

        return stale_devices


device_status_manager = DeviceStatusManager()
