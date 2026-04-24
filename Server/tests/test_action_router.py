import asyncio

import pytest

from src.services.action_router import ActionRouter, ActionStatus


class DummyHub:
    def __init__(self):
        self._client_connections = {}
        self.sent = []

    def is_device_connected(self, device_id: str) -> bool:
        return True

    async def send_to_device(self, device_id: str, message: dict) -> bool:
        self.sent.append((device_id, message))
        return True

    async def send_to_connection(self, connection_id: str, message: dict) -> bool:
        self.sent.append((connection_id, message))
        return True

    async def send_to_web_device(self, device_id: str, message: dict) -> bool:
        return True

    async def broadcast_to_web_consoles(self, message: dict, subscribed_only: bool = False) -> bool:
        return True

    async def broadcast_agent_progress(self, **kwargs) -> None:
        # Accepts any kwargs for session/run identity compatibility
        pass


@pytest.mark.asyncio
async def test_ack_then_observe_closes_same_round(monkeypatch):
    router = ActionRouter(DummyHub())
    await router.start()

    pending = await router.send_action(
        task_id="task-1",
        device_id="device-1",
        action={"action": "Tap", "element": [1, 2]},
        step_number=1,
        round_version=7,
    )

    ack_ok = await router.handle_ack(
        {
            "type": "ack",
            "version": "7",
            "ref_msg_id": pending.sent_msg_id,
            "payload": {
                "accepted": True,
                "device_id": "device-1",
            },
        }
    )
    assert ack_ok is True
    assert pending.status == ActionStatus.ACKNOWLEDGED

    observe_ok = await router.handle_observe_result(
        {
            "task_id": "task-1",
            "device_id": "device-1",
            "step_number": 1,
            "version": 7,
            "result": "done",
            "success": True,
            "screenshot": "abc",
        }
    )
    assert observe_ok is True

    result = await router.wait_for_result(pending.action_id)
    assert result["success"] is True
    assert result["version"] == 7
    assert result["device_id"] == "device-1"

    await router.stop()


@pytest.mark.asyncio
async def test_duplicate_ack_and_observe_are_idempotent():
    router = ActionRouter(DummyHub())
    await router.start()

    pending = await router.send_action(
        task_id="task-2",
        device_id="device-2",
        action={"action": "Tap"},
        step_number=2,
        round_version=9,
    )

    ack_msg = {
        "type": "ack",
        "version": "9",
        "ref_msg_id": pending.sent_msg_id,
        "payload": {"accepted": True, "device_id": "device-2"},
    }
    assert await router.handle_ack(ack_msg) is True
    assert await router.handle_ack(ack_msg) is True

    observe_msg = {
        "task_id": "task-2",
        "device_id": "device-2",
        "step_number": 2,
        "version": 9,
        "result": "ok",
        "success": True,
    }
    assert await router.handle_observe_result(observe_msg) is True
    assert await router.handle_observe_result(observe_msg) is True

    result = await router.wait_for_result(pending.action_id)
    assert result["success"] is True
    assert result["result"] == "ok"

    await router.stop()


@pytest.mark.asyncio
async def test_ack_timeout_result_type():
    router = ActionRouter(DummyHub())
    await router.start()

    result = await router.execute_action(
        task_id="task-3",
        device_id="device-3",
        action={"action": "Tap"},
        step_number=1,
        round_version=11,
        ack_timeout_seconds=0.01,
        observe_timeout_seconds=0.01,
    )

    assert result["success"] is False
    assert result["error_type"] == "ack_timeout"

    await router.stop()


@pytest.mark.asyncio
async def test_rejected_ack_short_circuits_round():
    router = ActionRouter(DummyHub())
    await router.start()

    pending = await router.send_action(
        task_id="task-4",
        device_id="device-4",
        action={"action": "Tap"},
        step_number=3,
        round_version=15,
    )

    await router.handle_ack(
        {
            "type": "ack",
            "version": "15",
            "ref_msg_id": pending.sent_msg_id,
            "payload": {
                "accepted": False,
                "device_id": "device-4",
                "error": "offline",
                "error_code": 1001,
            },
        }
    )

    result = await router.wait_for_result(pending.action_id)
    assert result["success"] is False
    assert result["error_type"] == "ack_rejected"
    assert result["error"] == "offline"

    await router.stop()
