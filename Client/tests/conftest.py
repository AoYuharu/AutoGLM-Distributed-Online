"""
Pytest configuration and shared fixtures.
"""
import sys
import os
from pathlib import Path
import pytest
from unittest.mock import Mock, AsyncMock, patch

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


@pytest.fixture
def mock_subprocess():
    """Mock subprocess module for ADB/HDC command testing."""
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.check_output") as mock_check, \
         patch("subprocess.Popen") as mock_popen:

        mock_run.return_value = Mock(
            stdout="",
            stderr="",
            returncode=0
        )
        mock_check.return_value = b""
        mock_popen.return_value = Mock(
            stdout=Mock(read=Mock(return_value=b"")),
            wait=Mock(return_value=0)
        )

        yield {
            "run": mock_run,
            "check_output": mock_check,
            "popen": mock_popen
        }


@pytest.fixture
def mock_websocket():
    """Mock WebSocket for network testing."""
    websocket = AsyncMock()
    websocket.recv = AsyncMock(return_value='{"type": "welcome", "session_id": "test-session"}')
    websocket.send = AsyncMock()
    websocket.close = AsyncMock()

    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = websocket
        yield websocket


@pytest.fixture
def sample_screenshot():
    """Sample screenshot bytes for testing."""
    # Create a minimal PNG file (1x1 transparent pixel)
    return (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
        b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
        b'\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    )


@pytest.fixture
def app_packages():
    """Sample app package mapping."""
    return {
        "微信": "com.tencent.mm",
        "支付宝": "com.eg.android.AlipayGphone",
        "淘宝": "com.taobao.taobao4",
        "抖音": "com.ss.android.ugc.aweme",
        "小红书": "com.xingin.xhs",
    }


@pytest.fixture
def device_info_android():
    """Sample Android device info."""
    return {
        "device_id": "R5CR12345ABC",
        "platform": "android",
        "model": "MI 13 Pro",
        "connection": "usb",
        "os_version": "Android 14",
        "screen_size": (1080, 2400),
        "api_level": 34,
    }


@pytest.fixture
def device_info_harmonyos():
    """Sample HarmonyOS device info."""
    return {
        "device_id": "HW-P50-12345",
        "platform": "harmonyos",
        "model": "HUAWEI P50",
        "connection": "wifi",
        "os_version": "HarmonyOS 4.0",
    }


@pytest.fixture
def task_message_data():
    """Sample task message data."""
    return {
        "msg_id": "uuid-test-001",
        "type": "task",
        "version": "1.0",
        "timestamp": "2024-03-15T10:30:00Z",
        "task_id": "task_20240315_001",
        "target": {
            "device_id": "R5CR12345ABC",
            "platform": "android"
        },
        "model_config": {
            "base_url": "http://localhost:8000/v1",
            "model": "autoglm-phone-9b",
        },
        "task": "打开微信搜索附近的人",
        "max_steps": 100,
        "priority": 1,
        "timeouts": {
            "step_max_seconds": 60,
            "task_max_seconds": 3600
        },
        "screenshot_config": {
            "upload_on": ["error", "finish", "interrupted", "interval:5"]
        }
    }


@pytest.fixture
def interrupt_message_data():
    """Sample interrupt message data."""
    return {
        "msg_id": "uuid-test-002",
        "type": "interrupt",
        "version": "1.0",
        "timestamp": "2024-03-15T10:30:00Z",
        "task_id": "task_20240315_001",
        "reason": "user_cancelled"
    }


@pytest.fixture
def mock_device_adapter():
    """Mock device adapter for testing."""
    adapter = Mock()
    adapter.device_id = "test-device-001"
    adapter.is_available = True

    # Mock capabilities
    adapter.capabilities = Mock(
        platform="android",
        screenshot=True,
        input_text=True,
        system_buttons=["back", "home"],
        battery=True,
        screen_size=(1080, 2400),
        os_version="Android 14",
        supported_apps=["微信", "支付宝"],
        api_level=34,
        device_name="Test Device"
    )

    # Mock methods
    adapter.check_capabilities = AsyncMock(return_value=adapter.capabilities)
    adapter.health_check = AsyncMock(return_value=True)
    adapter.get_screenshot = Mock(return_value=b"fake_screenshot")
    adapter.execute_action = Mock(return_value=Mock(success=True, should_finish=False))

    return adapter


@pytest.fixture
def mock_model_client():
    """Mock model client for testing."""
    client = Mock()
    client.inference = AsyncMock(return_value='do(action="Tap", element={"x": 500, "y": 300})')
    client.configure = Mock()
    return client
