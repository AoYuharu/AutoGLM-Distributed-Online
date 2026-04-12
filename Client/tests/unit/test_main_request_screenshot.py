import asyncio
import base64
from unittest.mock import AsyncMock, Mock, patch

import pytest

from main import DistributedClient
from src.network.messages import AckErrorCode


@pytest.fixture()
def client_instance():
    with patch.object(DistributedClient, "_generate_client_id", return_value="test-client"):
        client = DistributedClient(server_url="ws://localhost:8080/ws")
    client.http_client = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_handle_request_screenshot_sends_ack_before_bootstrap_observe_result(client_instance):
    order = []

    adapter = Mock()

    def fake_get_screenshot():
        order.append("screenshot")
        return b"fake_screenshot"

    adapter.get_screenshot.side_effect = fake_get_screenshot
    client_instance.device_adapters["device-1"] = adapter

    async def fake_send_ack(**kwargs):
        order.append("ack")

    async def fake_send_observe_result(**kwargs):
        order.append("observe")

    client_instance._send_ack = AsyncMock(side_effect=fake_send_ack)
    client_instance.send_observe_result = AsyncMock(side_effect=fake_send_observe_result)

    message = {
        "msg_id": "msg-bootstrap-1",
        "type": "request_screenshot",
        "payload": {
            "task_id": "task-1",
            "device_id": "device-1",
            "step_number": 0,
            "phase": "observe",
            "purpose": "bootstrap",
        },
    }

    await client_instance._handle_request_screenshot(message)

    assert order == ["ack", "screenshot", "observe"]
    client_instance._send_ack.assert_awaited_once_with(
        ref_msg_id="msg-bootstrap-1",
        accepted=True,
        device_id="device-1",
    )
    client_instance.send_observe_result.assert_awaited_once_with(
        task_id="task-1",
        device_id="device-1",
        step_number=0,
        screenshot=base64.b64encode(b"fake_screenshot").decode(),
        result="screenshot_captured",
        success=True,
    )


@pytest.mark.asyncio
async def test_handle_request_screenshot_unknown_device_rejects_ack_and_skips_observe_result(client_instance):
    client_instance._send_ack = AsyncMock()
    client_instance.send_observe_result = AsyncMock()
    message = {
        "msg_id": "msg-bootstrap-missing",
        "type": "request_screenshot",
        "payload": {
            "task_id": "task-unknown",
            "device_id": "missing-device",
        },
    }

    with patch("main.logger.warning") as warning_mock:
        await client_instance._handle_request_screenshot(message)

    client_instance._send_ack.assert_awaited_once_with(
        ref_msg_id="msg-bootstrap-missing",
        accepted=False,
        device_id="missing-device",
        error="Device not found",
        error_code=AckErrorCode.DEVICE_OFFLINE.value,
    )
    client_instance.send_observe_result.assert_not_awaited()
    warning_mock.assert_called_once()
    assert "task_id=task-unknown" in warning_mock.call_args[0][0]
    assert "device_id=missing-device" in warning_mock.call_args[0][0]
    assert client_instance._send_ack.await_args_list[0].kwargs["error_code"] == AckErrorCode.DEVICE_OFFLINE.value


@pytest.mark.asyncio
async def test_on_ws_message_routes_request_screenshot_to_async_handler(client_instance):
    client_instance._handle_request_screenshot = AsyncMock()
    client_instance._send_ack = AsyncMock()

    message = {
        "type": "request_screenshot",
        "msg_id": "msg-1",
        "payload": {
            "task_id": "task-1",
            "device_id": "device-1",
        },
    }

    created = []
    original_create_task = asyncio.create_task

    def tracking_create_task(coro):
        task = original_create_task(coro)
        created.append(task)
        return task

    with patch("main.asyncio.create_task", side_effect=tracking_create_task):
        client_instance._on_ws_message(message)
        await asyncio.gather(*created)

    client_instance._handle_request_screenshot.assert_awaited_once_with(message)
    client_instance._send_ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_action_cmd_sends_failed_observe_result_when_execute_action_raises(client_instance):
    adapter = Mock()
    adapter.execute_action.side_effect = RuntimeError("boom")
    adapter.get_screenshot.return_value = b"after-failure"
    client_instance.device_adapters["device-1"] = adapter
    client_instance._send_ack = AsyncMock()
    client_instance.send_observe_result = AsyncMock()

    message = {
        "msg_id": "msg-1",
        "type": "action_cmd",
        "version": "7",
        "payload": {
            "task_id": "task-1",
            "device_id": "device-1",
            "step_number": 3,
            "action": {"action": "tap", "element": {"x": 1, "y": 2}},
        },
    }

    with patch("main.asyncio.sleep", new=AsyncMock()):
        await client_instance._handle_action_cmd(message)

    client_instance._send_ack.assert_awaited_once_with(
        ref_msg_id="msg-1",
        accepted=True,
        device_id="device-1",
        version=7,
    )
    client_instance.send_observe_result.assert_awaited_once_with(
        task_id="task-1",
        device_id="device-1",
        step_number=3,
        screenshot=base64.b64encode(b"after-failure").decode(),
        result="Action failed: boom",
        success=False,
        error="Action failed: boom",
        version=7,
    )


@pytest.mark.asyncio
async def test_handle_action_cmd_sends_observe_result_when_screenshot_fails(client_instance):
    adapter = Mock()
    adapter.execute_action.return_value = Mock(success=True, should_finish=False, message=None)
    adapter.get_screenshot.side_effect = RuntimeError("capture broke")
    client_instance.device_adapters["device-1"] = adapter
    client_instance._send_ack = AsyncMock()
    client_instance.send_observe_result = AsyncMock()

    message = {
        "msg_id": "msg-2",
        "type": "action_cmd",
        "version": "8",
        "payload": {
            "task_id": "task-2",
            "device_id": "device-1",
            "step_number": 4,
            "action": {"action": "tap", "element": {"x": 10, "y": 20}},
        },
    }

    with patch("main.asyncio.sleep", new=AsyncMock()):
        await client_instance._handle_action_cmd(message)

    client_instance.send_observe_result.assert_awaited_once_with(
        task_id="task-2",
        device_id="device-1",
        step_number=4,
        screenshot=None,
        result="Action succeeded; Screenshot failed: capture broke",
        success=True,
        error="Screenshot failed: capture broke",
        version=8,
    )


@pytest.mark.asyncio
async def test_build_observe_payload_appends_screenshot_error_for_failed_action(client_instance):
    payload = client_instance._build_observe_payload(
        result=Mock(success=False, message="Action failed: tap: device offline"),
        screenshot=None,
        screenshot_error="capture broke",
    )

    assert payload == {
        "screenshot": None,
        "result": "Action failed: tap: device offline; Screenshot failed: capture broke",
        "success": False,
        "error": "Action failed: tap: device offline; Screenshot failed: capture broke",
    }


@pytest.mark.asyncio
async def test_handle_action_cmd_deduplicates_after_first_execution(client_instance):
    adapter = Mock()
    adapter.execute_action.return_value = Mock(success=True, should_finish=False, message=None)
    adapter.get_screenshot.return_value = b"img"
    client_instance.device_adapters["device-1"] = adapter
    client_instance._send_ack = AsyncMock()
    client_instance.send_observe_result = AsyncMock()

    message = {
        "msg_id": "msg-3",
        "type": "action_cmd",
        "version": "9",
        "payload": {
            "task_id": "task-3",
            "device_id": "device-1",
            "step_number": 2,
            "action": {"action": "tap", "element": {"x": 10, "y": 20}},
        },
    }

    with patch("main.asyncio.sleep", new=AsyncMock()):
        await client_instance._handle_action_cmd(message)
        await client_instance._handle_action_cmd(message)

    assert client_instance._send_ack.await_count == 2
    assert client_instance.send_observe_result.await_count == 1
    adapter.execute_action.assert_called_once()
    adapter.get_screenshot.assert_called_once()


@pytest.mark.asyncio
async def test_handle_action_cmd_rejects_unknown_device_before_execution(client_instance):
    client_instance._send_ack = AsyncMock()
    client_instance.send_observe_result = AsyncMock()

    message = {
        "msg_id": "msg-4",
        "type": "action_cmd",
        "version": "10",
        "payload": {
            "task_id": "task-4",
            "device_id": "missing-device",
            "step_number": 1,
            "action": {"action": "tap"},
        },
    }

    await client_instance._handle_action_cmd(message)

    client_instance._send_ack.assert_awaited_once_with(
        ref_msg_id="msg-4",
        accepted=False,
        device_id="missing-device",
        error="Device not found",
        error_code=AckErrorCode.DEVICE_OFFLINE.value,
        version=10,
    )
    client_instance.send_observe_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_action_cmd_ignores_non_numeric_version(client_instance):
    client_instance._send_ack = AsyncMock()
    client_instance.send_observe_result = AsyncMock()

    await client_instance._handle_action_cmd(
        {
            "msg_id": "msg-5",
            "type": "action_cmd",
            "version": "v1",
            "payload": {
                "task_id": "task-5",
                "device_id": "device-1",
                "step_number": 1,
                "action": {"action": "tap"},
            },
        }
    )

    client_instance._send_ack.assert_not_awaited()
    client_instance.send_observe_result.assert_not_awaited()
