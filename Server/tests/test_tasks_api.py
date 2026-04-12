from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models.models import Client, Device
from src.services.file_storage import file_storage
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

    storage_state = {}

    def fake_load_chat_history(device_id):
        return list(storage_state.get(device_id, []))

    def fake_save_chat_history(device_id, messages):
        storage_state[device_id] = list(messages)

    def fake_append_chat_message(device_id, message):
        storage_state.setdefault(device_id, []).append(dict(message))

    monkeypatch.setattr(file_storage, "load_chat_history", fake_load_chat_history)
    monkeypatch.setattr(file_storage, "save_chat_history", fake_save_chat_history)
    monkeypatch.setattr(file_storage, "append_chat_message", fake_append_chat_message)
    monkeypatch.setattr(file_storage, "get_screenshots", lambda device_id: [])

    yield TestClient(app), TestingSessionLocal, storage_state

    app.dependency_overrides.clear()


class _SessionTask:
    task_id = "task-session"
    is_active = True
    status = type("Status", (), {"value": "running"})()
    instruction = "restore timeline"
    current_step = 3
    max_steps = 10
    max_observe_error_retries = 2
    consecutive_observe_error_count = 0

    def is_waiting_observe_error_decision(self):
        return False

    def get_latest_error_reason(self):
        return None

    def get_observe_error_prompt_payload(self):
        return None


def test_device_session_and_chat_history_pass_through_progress_metadata(client_with_db, monkeypatch):
    client, _SessionLocal, storage_state = client_with_db
    storage_state["device-1"] = [
        {
            "id": "msg_user",
            "role": "user",
            "content": "restore timeline",
            "created_at": "2026-04-10T00:00:00",
            "task_id": "task-session",
        },
        {
            "id": "msg_progress",
            "role": "agent",
            "content": "ACK 已收到",
            "created_at": "2026-04-10T00:00:01",
            "task_id": "task-session",
            "step_number": 2,
            "phase": "act",
            "stage": "ack_received",
            "progress_status_text": "ack_received",
            "progress_message": "ACK 已收到",
            "thinking": "tap confirm",
            "action_type": "Tap",
            "action_params": {"action": "Tap", "x": 10, "y": 20},
            "result": "accepted",
            "success": True,
            "error": None,
            "error_type": None,
            "version": 8,
            "error_code": None,
            "data": {
                "task_id": "task-session",
                "source": "test",
                "restore_metadata": {"resume_round": 2},
            },
        },
    ]
    monkeypatch.setattr(scheduler, "get_task", lambda device_id: _SessionTask())

    session_response = client.get("/api/v1/devices/device-1/session")
    assert session_response.status_code == 200, session_response.text
    session_body = session_response.json()

    progress = session_body["chat_history"][-1]
    assert progress["task_id"] == "task-session"
    assert progress["step_number"] == 2
    assert progress["phase"] == "act"
    assert progress["stage"] == "ack_received"
    assert progress["progress_status_text"] == "ack_received"
    assert progress["progress_message"] == "ACK 已收到"
    assert progress["result"] == "accepted"
    assert progress["success"] is True
    assert progress["version"] == 8
    assert progress["action_type"] == "Tap"
    assert progress["action_params"] == {"action": "Tap", "x": 10, "y": 20}
    assert progress["data"]["restore_metadata"]["resume_round"] == 2

    chat_response = client.get("/api/v1/devices/device-1/chat")
    assert chat_response.status_code == 200, chat_response.text
    chat_body = chat_response.json()

    assert chat_body["total"] == 2
    assert chat_body["messages"][-1]["stage"] == "ack_received"
    assert chat_body["messages"][-1]["action_type"] == "Tap"
    assert chat_body["messages"][-1]["data"]["source"] == "test"


@pytest.mark.asyncio
async def test_broadcast_agent_progress_and_status_persist_replay_milestones(monkeypatch):
    from src.services.websocket import WebSocketHub

    persisted = []

    def fake_append_chat_message(device_id, message):
        persisted.append((device_id, message))

    hub = WebSocketHub()
    monkeypatch.setattr(file_storage, "append_chat_message", fake_append_chat_message)

    await hub.broadcast_agent_progress(
        task_id="task-1",
        device_id="device-1",
        step_number=0,
        phase="observe",
        stage="waiting_ack",
        message="等待 bootstrap screenshot ACK",
    )
    # Bootstrap step 0 now persists canonical transport milestones
    assert len(persisted) == 1
    _, bootstrap_entry = persisted[-1]
    assert bootstrap_entry["task_id"] == "task-1"
    assert bootstrap_entry["step_number"] == 0
    assert bootstrap_entry["phase"] == "observe"
    assert bootstrap_entry["stage"] == "waiting_ack"
    assert bootstrap_entry["progress_message"] == "等待 bootstrap screenshot ACK"
    assert bootstrap_entry["progress_status_text"] == "waiting_ack"
    assert bootstrap_entry.get("thinking") is None
    assert bootstrap_entry.get("action_type") is None
    assert bootstrap_entry.get("action_params") is None
    assert bootstrap_entry.get("success") is None
    assert bootstrap_entry.get("result") == ""
    assert bootstrap_entry.get("error") is None
    assert bootstrap_entry.get("error_type") is None
    assert bootstrap_entry.get("version") is None

    observe_message = await hub.broadcast_agent_progress(
        task_id="task-1",
        device_id="device-1",
        step_number=0,
        phase="observe",
        stage="observe_received",
        message="初始截图已收到",
        result="screenshot_captured",
        success=True,
        screenshot="bootstrap-image",
    )
    assert observe_message["stage"] == "observe_received"
    assert len(persisted) == 2
    _, observe_entry = persisted[-1]
    assert observe_entry["step_number"] == 0
    assert observe_entry["stage"] == "observe_received"
    assert observe_entry["progress_message"] == "初始截图已收到"
    assert observe_entry["result"] == "screenshot_captured"
    assert observe_entry["success"] is True

    progress_message = await hub.broadcast_agent_progress(
        task_id="task-1",
        device_id="device-1",
        step_number=2,
        phase="act",
        stage="ack_received",
        message="ACK 已收到",
        version=6,
        reasoning="tap submit",
        action={"action": "Tap", "x": 1, "y": 2},
        result="accepted",
        success=True,
    )
    assert progress_message["stage"] == "ack_received"
    assert len(persisted) == 3

    device_id, entry = persisted[-1]
    assert device_id == "device-1"
    assert entry["task_id"] == "task-1"
    assert entry["step_number"] == 2
    assert entry["phase"] == "act"
    assert entry["stage"] == "ack_received"
    assert entry["progress_message"] == "ACK 已收到"
    assert entry["progress_status_text"] == "ack_received"
    # Transport milestones (ack_received) no longer carry reasoning/action per plan
    assert entry.get("thinking") is None
    assert entry.get("action_type") is None
    assert entry.get("action_params") is None
    assert entry["result"] == "accepted"
    assert entry["success"] is True
    assert entry["version"] == 6

    status_message = await hub.broadcast_agent_status(
        device_id="device-1",
        session_id="task-1",
        status="failed",
        message="Observation timeout",
        data={"task_id": "task-1", "error_type": "observe_timeout"},
    )
    assert status_message["status"] == "failed"
    assert len(persisted) == 4  # waiting_ack + observe_received + ack_received + failed status

    _, status_entry = persisted[-1]
    assert status_entry["task_id"] == "task-1"
    assert status_entry["progress_status_text"] == "failed"
    assert status_entry["progress_message"] == "Observation timeout"
    assert status_entry["data"]["error_type"] == "observe_timeout"

    await hub.broadcast_agent_status(
        device_id="device-1",
        session_id="task-1",
        status="running",
        message="Task running",
        data={"task_id": "task-1"},
    )
    assert len(persisted) == 4  # waiting_ack + observe_received + ack_received + failed status (running status not persisted)


@pytest.mark.asyncio
async def test_broadcast_task_update_routes_legacy_progress_to_canonical_protocol(monkeypatch):
    from src.services.websocket import WebSocketHub

    progress_calls = []

    async def fake_broadcast_agent_progress(**kwargs):
        progress_calls.append(kwargs)

    hub = WebSocketHub()
    monkeypatch.setattr(hub, "broadcast_agent_progress", fake_broadcast_agent_progress)

    await hub.broadcast_task_update(
        task_id="task-9",
        device_id="device-9",
        update={
            "stage": "reason_complete",
            "step_number": 4,
            "message": "Reasoning finished",
            "reasoning": "tap submit",
            "version": 3,
        },
    )

    assert len(progress_calls) == 1
    call = progress_calls[0]
    assert call["task_id"] == "task-9"
    assert call["device_id"] == "device-9"
    assert call["step_number"] == 4
    assert call["stage"] == "reason_complete"
    assert call["phase"] == "reason"
    assert call["message"] == "Reasoning finished"
    assert call["reasoning"] == "tap submit"
    assert call["version"] == 3


@pytest.mark.asyncio
async def test_broadcast_task_update_routes_legacy_status_to_canonical_protocol(monkeypatch):
    from src.services.websocket import WebSocketHub

    status_calls = []

    async def fake_broadcast_agent_status(**kwargs):
        status_calls.append(kwargs)

    hub = WebSocketHub()
    monkeypatch.setattr(hub, "broadcast_agent_status", fake_broadcast_agent_status)

    await hub.broadcast_task_update(
        task_id="task-10",
        device_id="device-10",
        update={
            "status": "completed",
            "message": "Task completed",
            "data": {"task_id": "task-10", "summary": "done"},
        },
    )

    assert len(status_calls) == 1
    call = status_calls[0]
    assert call["device_id"] == "device-10"
    assert call["session_id"] == "task-10"
    assert call["status"] == "completed"
    assert call["message"] == "Task completed"
    assert call["data"]["summary"] == "done"


@pytest.mark.asyncio
async def test_bootstrap_observe_http_skips_canonical_pending_round_router(client_with_db, monkeypatch):
    from src.api import tasks as tasks_api
    from src.services.action_router import action_router

    client, _SessionLocal, _storage_state = client_with_db
    scheduler_calls = []
    routed_payloads = []

    async def fake_set_observe_result(device_id, screenshot, observation, **kwargs):
        scheduler_calls.append(
            {
                "device_id": device_id,
                "screenshot": screenshot,
                "observation": observation,
                **kwargs,
            }
        )

    async def fake_handle_observe_result(payload):
        routed_payloads.append(payload)
        return True

    monkeypatch.setattr(scheduler, "set_observe_result", fake_set_observe_result)
    monkeypatch.setattr(action_router, "handle_observe_result", fake_handle_observe_result)
    monkeypatch.setattr(file_storage, "save_screenshot", lambda *args, **kwargs: "screenshots/step_0_test.png")
    monkeypatch.setattr(file_storage, "append_adb_log", lambda *args, **kwargs: None)

    response = client.post(
        "/api/v1/observe",
        json={
            "msg_id": "observe-bootstrap-1",
            "type": "observe_result",
            "version": "3",
            "payload": {
                "task_id": "task-bootstrap",
                "device_id": "device-1",
                "step_number": 0,
                "screenshot": "bootstrap-image",
                "result": "bootstrap observation",
                "success": True,
                "version": 3,
            },
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "success": True,
        "message": "Observe result recorded",
        "data": None,
    }
    assert len(scheduler_calls) == 1
    assert scheduler_calls[0]["device_id"] == "device-1"
    assert scheduler_calls[0]["step_number"] == 0
    assert scheduler_calls[0]["round_version"] == 3
    assert scheduler_calls[0]["screenshot_path"] == "screenshots/step_0_test.png"
    assert routed_payloads == []
