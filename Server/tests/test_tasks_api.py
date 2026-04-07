from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base
from src.main import app
from src.models.models import Client, Device, Task
from src.database import get_db
from src.services.device_status_manager import device_status_manager
from src.services.react_scheduler import scheduler


@pytest.fixture()
def client_with_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    db = TestingSessionLocal()
    client_row = Client(client_id="client-1", name="client", api_key="api-key-1")
    db.add(client_row)
    db.flush()
    device_row = Device(
        device_id="device-1",
        client_id=client_row.id,
        platform="android",
        model="device",
        status="idle",
        last_heartbeat=datetime.utcnow(),
    )
    db.add(device_row)
    db.commit()
    db.close()

    class DummyTask:
        def __init__(self, task_id: str):
            self.task_id = task_id
            self.is_active = True
            self.phase = None
            self.react_records = []

    scheduled = {}

    def fake_submit_task(device_id, task_id, instruction, mode="normal", max_steps=100, **kwargs):
        scheduled[device_id] = {
            "task_id": task_id,
            "instruction": instruction,
            "mode": mode,
            "max_steps": max_steps,
        }
        return DummyTask(task_id)

    async def fake_interrupt_task(device_id):
        scheduled.pop(device_id, None)

    async def fake_set_observe_result(device_id, screenshot, observation):
        scheduled.setdefault("observe", []).append((device_id, screenshot, observation))

    def fake_requeue_task(device_id):
        scheduled.setdefault("requeued", []).append(device_id)

    monkeypatch.setattr(scheduler, "submit_task", fake_submit_task)
    monkeypatch.setattr(scheduler, "interrupt_task", fake_interrupt_task)
    monkeypatch.setattr(scheduler, "set_observe_result", fake_set_observe_result)
    monkeypatch.setattr(scheduler, "requeue_task", fake_requeue_task)

    yield TestClient(app), TestingSessionLocal, scheduled

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_task_marks_device_busy_and_rejects_second_task(client_with_db):
    client, SessionLocal, scheduled = client_with_db
    await device_status_manager.set_idle("device-1")

    response = client.post(
        "/api/v1/tasks",
        json={"device_id": "device-1", "instruction": "do something", "max_steps": 5},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_id"].startswith("task_")
    assert scheduled["device-1"]["max_steps"] == 5

    entry = await device_status_manager.get_entry("device-1")
    assert entry is not None
    assert entry.status.value == "busy"
    assert entry.current_task_id == body["task_id"]

    second = client.post(
        "/api/v1/tasks",
        json={"device_id": "device-1", "instruction": "second task"},
    )
    assert second.status_code == 400


def test_observe_updates_db_and_scheduler(client_with_db):
    client, SessionLocal, scheduled = client_with_db
    db = SessionLocal()
    task = db.query(Task).filter(Task.task_id == scheduled.get("device-1", {}).get("task_id", "missing")).first()
    if task is None:
        task = Task(
            task_id="task-observe",
            device_id=db.query(Device).filter(Device.device_id == "device-1").one().id,
            client_id=db.query(Client).filter(Client.client_id == "client-1").one().id,
            instruction="observe",
            status="pending",
        )
        db.add(task)
        db.commit()
        task_id = "task-observe"
    else:
        task_id = task.task_id
    db.close()

    response = client.post(
        "/api/v1/tasks/observe",
        json={
            "msg_id": "m1",
            "type": "observe_result",
            "version": "3",
            "payload": {
                "task_id": task_id,
                "device_id": "device-1",
                "step_number": 1,
                "screenshot": "abc",
                "result": "ok",
                "success": True,
                "version": 3,
            },
        },
    )
    assert response.status_code == 200, response.text

    db = SessionLocal()
    task_row = db.query(Task).filter(Task.task_id == task_id).one()
    assert task_row.current_step == 1
    assert task_row.status == "running"
    db.close()

    assert ("device-1", "abc", "ok") in scheduled["observe"]
    assert "device-1" in scheduled["requeued"]


@pytest.mark.asyncio
async def test_device_status_idle_heartbeat_preserves_active_task(client_with_db, monkeypatch):
    client, SessionLocal, scheduled = client_with_db
    await device_status_manager.set_busy("device-1", "task-live")

    class ActiveTask:
        task_id = "task-live"
        is_active = True

    monkeypatch.setattr(scheduler, "get_task", lambda device_id: ActiveTask())

    response = client.post(
        "/api/v1/devices/status",
        json={
            "msg_id": "status-1",
            "type": "device_status",
            "version": "1.0",
            "client_id": "client-1",
            "payload": {
                "devices": [
                    {
                        "device_id": "device-1",
                        "status": "idle",
                        "platform": "android",
                    }
                ]
            },
        },
    )

    assert response.status_code == 200, response.text

    entry = await device_status_manager.get_entry("device-1")
    assert entry is not None
    assert entry.status.value == "busy"
    assert entry.current_task_id == "task-live"


@pytest.mark.asyncio
async def test_scheduler_reason_failed_marks_task_failed_without_execute_act(monkeypatch):
    from src.services.react_scheduler import DeviceTask, ReActScheduler, TaskStatus

    scheduler_instance = ReActScheduler(core_threads=1, max_threads=1)
    task = DeviceTask(device_id="device-1", task_id="task-1", instruction="do something")
    task.initialize()
    scheduler_instance._device_tasks["device-1"] = task

    update_calls = []
    idle_calls = []
    removed = []

    async def fake_execute_reason():
        return "AI模型响应超时", {
            "action": "error",
            "message": "model_timeout",
            "error_type": "reason_failed",
        }

    async def fake_execute_act(action, round_version):
        raise AssertionError("execute_act should not run when reasoning fails")

    async def fake_update_task_db(task_id, **kwargs):
        update_calls.append((task_id, kwargs))

    async def fake_broadcast_phase_start(*args, **kwargs):
        return None

    async def fake_broadcast_phase_end(*args, **kwargs):
        return None

    async def fake_set_device_idle(device_id):
        idle_calls.append(device_id)

    def fake_remove_task(device_id):
        removed.append(device_id)
        scheduler_instance._device_tasks.pop(device_id, None)

    class DummyHub:
        def __init__(self):
            self.statuses = []

        async def broadcast_agent_status(self, **kwargs):
            self.statuses.append(kwargs)

    task.execute_reason = fake_execute_reason
    task.execute_act = fake_execute_act
    scheduler_instance._update_task_db = fake_update_task_db
    scheduler_instance._broadcast_phase_start = fake_broadcast_phase_start
    scheduler_instance._broadcast_phase_end = fake_broadcast_phase_end
    scheduler_instance._set_device_idle = fake_set_device_idle
    scheduler_instance.remove_task = fake_remove_task
    scheduler_instance._ws_hub = DummyHub()

    result = await scheduler_instance.run_one_cycle("device-1")

    assert result is True
    assert task.status == TaskStatus.FAILED
    assert idle_calls == ["device-1"]
    assert removed == ["device-1"]
    assert update_calls
    _, kwargs = update_calls[-1]
    assert kwargs["status"] == "failed"
    assert kwargs["error_message"] == "model_timeout"
    assert kwargs["result"]["error_type"] == "reason_failed"
    assert scheduler_instance._ws_hub.statuses[-1]["status"] == "failed"
    assert scheduler_instance._ws_hub.statuses[-1]["message"] == "model_timeout"
    assert scheduler_instance.get_task("device-1") is None
    scheduler_instance.executor.shutdown(wait=False)
