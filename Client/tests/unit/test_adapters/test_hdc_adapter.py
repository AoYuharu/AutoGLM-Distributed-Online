"""
HDC Adapter 单元测试 - TDD 风格

按照 TDD 流程：
1. 先写失败的测试
2. 再实现代码让测试通过
"""
import pytest
from unittest.mock import Mock, patch, MagicMock


class TestHDCAdapterBase:
    """HDC 适配器基础测试"""

    def test_adapter_initialization(self):
        """测试适配器初始化"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device-001")
        assert adapter.device_id == "test-device-001"
        assert adapter.hdc_path == "hdc"
        assert adapter._capabilities is None

    def test_adapter_with_custom_hdc_path(self):
        """测试使用自定义 HDC 路径"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device-001", hdc_path="/usr/local/bin/hdc")
        assert adapter.hdc_path == "/usr/local/bin/hdc"

    def test_hdc_prefix_without_device_id(self):
        """测试无设备 ID 时的 HDC 前缀"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("")
        assert adapter._hdc_prefix == ["hdc"]

    def test_hdc_prefix_with_device_id(self):
        """测试有设备 ID 时的 HDC 前缀"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("device-001")
        assert adapter._hdc_prefix == ["hdc", "-t", "device-001"]

    def test_platform_is_harmonyos(self):
        """测试平台类型为 HarmonyOS"""
        from src.adapters.hdc_adapter import HDCAdapter
        from src.adapters.base import Platform

        adapter = HDCAdapter("test-device")
        assert adapter.platform == Platform.HARMONYOS

    def test_is_available_false_initially(self):
        """测试初始状态不可用"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")
        assert adapter.is_available is False


class TestHDCAdapterHealthCheck:
    """HDC 适配器健康检查测试"""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_device_online(self):
        """设备在线时健康检查返回 True"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, '_run_hdc') as mock_run:
            mock_run.return_value = Mock(returncode=0)
            result = await adapter.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_device_offline(self):
        """设备离线时健康检查返回 False"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, '_run_hdc') as mock_run:
            mock_run.return_value = Mock(returncode=1, stderr="device offline")
            result = await adapter.health_check()
            assert result is False


class TestHDCAdapterActions:
    """HDC 适配器动作执行测试"""

    def test_execute_tap_action(self):
        """执行 Tap 动作"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, 'tap') as mock_tap:
            mock_tap.return_value = None
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "tap",
                "element": {"x": 500, "y": 300}
            })

            mock_tap.assert_called_once_with(500, 300)
            assert result.success is True

    def test_execute_swipe_action(self):
        """执行 Swipe 动作"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, 'swipe') as mock_swipe:
            mock_swipe.return_value = None
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "swipe",
                "start": {"x": 500, "y": 500},
                "end": {"x": 500, "y": 800}
            })

            mock_swipe.assert_called_once()
            assert result.success is True

    def test_execute_back_action(self):
        """执行 Back 动作"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, 'back') as mock_back:
            mock_back.return_value = None
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "back"
            })

            mock_back.assert_called_once()
            assert result.success is True

    def test_execute_finish_action(self):
        """执行 Finish 动作"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        result = adapter.execute_action({
            "_metadata": "finish",
            "message": "任务完成"
        })

        assert result.success is True
        assert result.should_finish is True

    def test_execute_tap_action_with_server_payload(self):
        """执行服务端原生动作 payload"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, 'tap') as mock_tap:
            mock_tap.return_value = None
            result = adapter.execute_action({
                "action": "tap",
                "element": {"x": 500, "y": 300}
            })

            mock_tap.assert_called_once_with(500, 300)
            assert result.success is True

    def test_execute_finish_action_with_server_payload(self):
        """执行服务端 finish payload"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        result = adapter.execute_action({
            "action": "finish",
            "message": "任务完成"
        })

        assert result.success is True
        assert result.should_finish is True


class TestHDCAdapterMethods:
    """HDC 适配器具体方法测试"""

    def test_tap_executes_hdc_command(self):
        """tap 方法执行正确的 HDC 命令"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, '_run_hdc') as mock_run:
            mock_run.return_value = Mock()
            adapter.tap(500, 800)

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "shell" in args
            assert "uitest" in args
            assert "uiInput" in args
            assert "click" in args
            assert "500" in args
            assert "800" in args

    def test_swipe_executes_hdc_command(self):
        """swipe 方法执行正确的 HDC 命令"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, '_run_hdc') as mock_run:
            mock_run.return_value = Mock()
            adapter.swipe(100, 200, 100, 400, duration_ms=500)

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "shell" in args
            assert "uitest" in args
            assert "uiInput" in args
            assert "swipe" in args

    def test_back_executes_hdc_keyevent(self):
        """back 执行 HDC keyEvent 命令"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, '_run_hdc') as mock_run:
            mock_run.return_value = Mock()
            adapter.back()

            args = mock_run.call_args[0][0]
            assert "keyEvent" in args
            assert "Back" in args

    def test_home_executes_hdc_keyevent(self):
        """home 执行 HDC keyEvent 命令"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, '_run_hdc') as mock_run:
            mock_run.return_value = Mock()
            adapter.home()

            args = mock_run.call_args[0][0]
            assert "keyEvent" in args
            assert "Home" in args

    def test_long_press_executes_hdc_longclick(self):
        """long_press 执行 HDC longClick 命令"""
        from src.adapters.hdc_adapter import HDCAdapter

        adapter = HDCAdapter("test-device")

        with patch.object(adapter, '_run_hdc') as mock_run:
            mock_run.return_value = Mock()
            adapter.long_press(500, 500, duration_ms=3000)

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "longClick" in args


class TestHDCDeviceList:
    """HDC 设备列表测试"""

    def test_list_devices_returns_list(self):
        """list_devices 返回设备列表"""
        from src.adapters.hdc_adapter import HDCAdapter

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                stdout="device-001\ndevice-002\n",
                returncode=0
            )

            devices = HDCAdapter.list_devices()
            assert len(devices) == 2
            assert devices[0].device_id == "device-001"

    def test_list_devices_handles_empty(self):
        """list_devices 处理空列表"""
        from src.adapters.hdc_adapter import HDCAdapter

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)

            devices = HDCAdapter.list_devices()
            assert len(devices) == 0

    def test_list_devices_handles_error(self):
        """list_devices 处理错误"""
        from src.adapters.hdc_adapter import HDCAdapter

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("HDC error")

            devices = HDCAdapter.list_devices()
            assert len(devices) == 0
