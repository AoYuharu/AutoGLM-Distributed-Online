import pytest

from src.services.device_status_manager import DeviceStatus, DeviceStatusManager


@pytest.mark.asyncio
async def test_try_acquire_then_release_clears_current_task_id():
    manager = DeviceStatusManager()
    await manager.set_idle("device-a")

    acquired = await manager.try_acquire_task("device-a", "task-a")
    assert acquired is True

    entry = await manager.get_entry("device-a")
    assert entry is not None
    assert entry.status == DeviceStatus.BUSY
    assert entry.current_task_id == "task-a"
    assert entry.current_session_id == "task-a"  # session_id defaults to task_id when not provided
    assert entry.current_run_id is None

    await manager.set_idle("device-a")
    entry = await manager.get_entry("device-a")
    assert entry.status == DeviceStatus.IDLE
    assert entry.current_task_id is None
    assert entry.current_session_id is None
    assert entry.current_run_id is None


@pytest.mark.asyncio
async def test_set_offline_clears_current_task_id():
    manager = DeviceStatusManager()
    await manager.set_idle("device-b")
    assert await manager.try_acquire_task("device-b", "task-b") is True

    await manager.set_offline("device-b")
    entry = await manager.get_entry("device-b")
    assert entry is not None
    assert entry.status == DeviceStatus.OFFLINE
    assert entry.current_task_id is None


@pytest.mark.asyncio
async def test_busy_device_cannot_be_acquired_twice():
    manager = DeviceStatusManager()
    await manager.set_idle("device-c")

    assert await manager.try_acquire_task("device-c", "task-c1") is True
    entry = await manager.get_entry("device-c")
    assert entry.current_session_id == "task-c1"
    assert await manager.try_acquire_task("device-c", "task-c2") is False
