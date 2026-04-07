"""
WDA 适配器测试
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
import asyncio

from src.adapters.wda_adapter import WDAAdapter
from src.adapters.base import DeviceCapabilities, Platform


class TestWDAAdapter:
    """WDA 适配器测试"""

    @pytest.fixture
    def adapter(self):
        """创建 WDA 适配器"""
        return WDAAdapter(
            device_id="test-device-udid",
            wda_url="http://localhost:8100",
        )

    def test_init(self, adapter):
        """测试初始化"""
        assert adapter.device_id == "test-device-udid"
        assert adapter.wda_url == "http://localhost:8100"
        assert adapter._platform == Platform.IOS
        assert adapter._session_id is None

    @pytest.mark.asyncio
    async def test_health_check_success(self, adapter):
        """测试心跳检测成功"""
        with patch.object(adapter, '_request', return_value={"status": "ok"}):
            result = await adapter.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, adapter):
        """测试心跳检测失败"""
        with patch.object(adapter, '_request', side_effect=Exception("Connection failed")):
            result = await adapter.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_check_capabilities(self, adapter):
        """测试能力检查"""
        with patch.object(adapter, '_get_device_info', return_value={"device_name": "iPhone 14", "os_version": "17.0"}):
            with patch.object(adapter, '_get_screen_size', return_value=(390, 844)):
                with patch.object(adapter, '_get_installed_apps', return_value=[]):
                    caps = await adapter.check_capabilities()

                    assert caps.platform == Platform.IOS
                    assert caps.screenshot is True
                    assert caps.input_text is True
                    assert caps.screen_size == (390, 844)
                    assert caps.os_version == "17.0"

    @pytest.mark.asyncio
    async def test_check_capabilities_cached(self, adapter):
        """测试能力检查缓存"""
        mock_caps = DeviceCapabilities(
            platform=Platform.IOS,
            screenshot=True,
            screen_size=(390, 844),
        )
        adapter._capabilities = mock_caps

        caps = await adapter.check_capabilities()
        assert caps == mock_caps

    def test_execute_action_finish(self, adapter):
        """测试执行 finish 动作"""
        result = adapter.execute_action({
            "_metadata": "finish",
            "message": "Task completed"
        })

        assert result.success is True
        assert result.should_finish is True
        assert result.message == "Task completed"

    def test_execute_action_tap_with_server_payload(self, adapter):
        """测试服务端原生动作 payload"""
        with patch.object(adapter, 'tap') as mock_tap:
            result = adapter.execute_action({
                "action": "tap",
                "element": {"x": 500, "y": 500}
            })

            assert result.success is True
            assert result.should_finish is False
            mock_tap.assert_called_once()

    def test_execute_action_finish_with_server_payload(self, adapter):
        """测试服务端 finish payload"""
        result = adapter.execute_action({
            "action": "finish",
            "message": "Task completed"
        })

        assert result.success is True
        assert result.should_finish is True
        assert result.message == "Task completed"

    def test_execute_action_unknown_type(self, adapter):
        """测试未知动作类型"""
        result = adapter.execute_action({
            "_metadata": "unknown"
        })

        assert result.success is False
        assert result.should_finish is False

    def test_execute_action_tap(self, adapter):
        """测试点击动作"""
        with patch.object(adapter, 'tap') as mock_tap:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "tap",
                "element": {"x": 500, "y": 500}
            })

            assert result.success is True
            assert result.should_finish is False
            mock_tap.assert_called_once()

    def test_execute_action_tap_no_element(self, adapter):
        """测试点击动作无坐标"""
        result = adapter.execute_action({
            "_metadata": "do",
            "action": "tap"
        })

        assert result.success is False
        assert "No element coordinates" in result.message

    def test_execute_action_double_tap(self, adapter):
        """测试双击动作"""
        with patch.object(adapter, 'double_tap') as mock_double_tap:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "double_tap",
                "element": {"x": 100, "y": 200}
            })

            assert result.success is True
            mock_double_tap.assert_called_once_with(100, 200)

    def test_execute_action_long_press(self, adapter):
        """测试长按动作"""
        with patch.object(adapter, 'long_press') as mock_long_press:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "long_press",
                "element": {"x": 300, "y": 400},
                "duration": 2000
            })

            assert result.success is True
            mock_long_press.assert_called_once_with(300, 400, 2000)

    def test_execute_action_swipe(self, adapter):
        """测试滑动动作"""
        with patch.object(adapter, 'swipe') as mock_swipe:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "swipe",
                "start": {"x": 500, "y": 800},
                "end": {"x": 500, "y": 200},
                "duration": 500
            })

            assert result.success is True
            mock_swipe.assert_called_once_with(500, 800, 500, 200, 500)

    def test_execute_action_swipe_missing_coords(self, adapter):
        """测试滑动动作缺失坐标"""
        result = adapter.execute_action({
            "_metadata": "do",
            "action": "swipe",
            "start": {"x": 500, "y": 800}
        })

        assert result.success is False
        assert "Missing swipe coordinates" in result.message

    def test_execute_action_type(self, adapter):
        """测试输入文本动作"""
        with patch.object(adapter, 'type_text') as mock_type:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "type",
                "text": "hello world"
            })

            assert result.success is True
            mock_type.assert_called_once_with("hello world")

    def test_execute_action_launch(self, adapter):
        """测试启动应用动作"""
        with patch.object(adapter, 'launch_app', return_value=True) as mock_launch:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "com.apple.mobilesafari"
            })

            assert result.success is True
            mock_launch.assert_called_once_with("com.apple.mobilesafari")

    def test_execute_action_launch_no_bundle_id(self, adapter):
        """测试启动应用动作无 bundle ID"""
        result = adapter.execute_action({
            "_metadata": "do",
            "action": "launch"
        })

        assert result.success is False
        assert "No bundle_id specified" in result.message

    def test_execute_action_wait(self, adapter):
        """测试等待动作"""
        result = adapter.execute_action({
            "_metadata": "do",
            "action": "wait",
            "duration": "2"
        })

        assert result.success is True

    def test_execute_action_unknown_action(self, adapter):
        """测试未知动作"""
        result = adapter.execute_action({
            "_metadata": "do",
            "action": "unknown_action"
        })

        assert result.success is False
        assert "Unknown action" in result.message

    def test_convert_coords(self, adapter):
        """测试坐标转换"""
        adapter._capabilities = DeviceCapabilities(
            platform=Platform.IOS,
            screen_size=(390, 844)
        )

        x, y = adapter._convert_coords({"x": 500, "y": 500})
        assert x == 195  # 500/1000 * 390
        assert y == 422  # 500/1000 * 844

    def test_convert_coords_no_caps(self, adapter):
        """测试无能力信息时的坐标转换"""
        adapter._capabilities = None

        x, y = adapter._convert_coords({"x": 500, "y": 500})
        assert x == 500
        assert y == 500

    @patch('src.adapters.wda_adapter.requests.get')
    def test_get_screenshot(self, mock_get, adapter):
        """测试获取截图"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        }
        mock_get.return_value = mock_response

        screenshot = adapter.get_screenshot()
        assert len(screenshot) > 0
        mock_get.assert_called_once()

    @patch('src.adapters.wda_adapter.requests.get')
    def test_get_screenshot_failure(self, mock_get, adapter):
        """测试获取截图失败"""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError, match="Screenshot failed"):
            adapter.get_screenshot()

    @patch('src.adapters.wda_adapter.requests.post')
    @patch.object(WDAAdapter, '_ensure_session', new_callable=lambda: AsyncMock(return_value="test-session"))
    def test_tap(self, mock_ensure, mock_post, adapter):
        """测试点击"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        adapter.tap(100, 200)
        assert mock_post.called

    @patch('src.adapters.wda_adapter.requests.post')
    @patch.object(WDAAdapter, '_ensure_session', new_callable=lambda: AsyncMock(return_value="test-session"))
    def test_home(self, mock_ensure, mock_post, adapter):
        """测试 Home 键"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        adapter.home()
        assert mock_post.called

    @patch('src.adapters.wda_adapter.requests.post')
    @patch.object(WDAAdapter, '_ensure_session', new_callable=lambda: AsyncMock(return_value="test-session"))
    def test_back(self, mock_ensure, mock_post, adapter):
        """测试返回键"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        adapter.back()
        assert mock_post.called

    @patch('src.adapters.wda_adapter.requests.post')
    @patch.object(WDAAdapter, '_ensure_session', new_callable=lambda: AsyncMock(return_value="test-session"))
    def test_launch_app(self, mock_ensure, mock_post, adapter):
        """测试启动应用"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        result = adapter.launch_app("com.apple.mobilesafari")
        assert result is True

    @patch('src.adapters.wda_adapter.requests.post')
    @patch.object(WDAAdapter, '_ensure_session', new_callable=lambda: AsyncMock(return_value="test-session"))
    def test_type_text(self, mock_ensure, mock_post, adapter):
        """测试输入文本"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        adapter.type_text("hello")
        assert mock_post.called
