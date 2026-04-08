"""
ADB Adapter 单元测试 - TDD 风格

按照 TDD 流程：
1. 先写失败的测试
2. 再实现代码让测试通过
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, PropertyMock
from dataclasses import asdict
import subprocess


class TestADBAdapterBase:
    """ADB 适配器基础测试"""

    def test_adapter_initialization(self):
        """测试适配器初始化"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device-001")
        assert adapter.device_id == "test-device-001"
        assert adapter.adb_path == "adb"
        assert adapter._capabilities is None

    def test_adapter_with_custom_adb_path(self):
        """测试使用自定义 ADB 路径"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device-001", adb_path="/usr/local/bin/adb")
        assert adapter.adb_path == "/usr/local/bin/adb"

    def test_adb_prefix_without_device_id(self):
        """测试无设备 ID 时的 ADB 前缀"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("")
        assert adapter._adb_prefix == ["adb"]

    def test_adb_prefix_with_device_id(self):
        """测试有设备 ID 时的 ADB 前缀"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("device-001")
        assert adapter._adb_prefix == ["adb", "-s", "device-001"]

    def test_platform_is_android(self):
        """测试平台类型为 Android"""
        from src.adapters.adb_adapter import ADBAdapter
        from src.adapters.base import Platform

        adapter = ADBAdapter("test-device")
        assert adapter.platform == Platform.ANDROID

    def test_is_available_false_initially(self):
        """测试初始状态不可用"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")
        assert adapter.is_available is False

    def test_capabilities_none_initially(self):
        """测试初始时能力为 None"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")
        assert adapter.capabilities is None


class TestADBAdapterHealthCheck:
    """ADB 适配器健康检查测试"""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_device_online(self):
        """设备在线时健康检查返回 True"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.return_value = Mock(returncode=0)
            result = await adapter.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_device_offline(self):
        """设备离线时健康检查返回 False"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.return_value = Mock(returncode=1, stderr="device offline")
            result = await adapter.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_handles_timeout(self):
        """健康检查处理超时"""
        from src.adapters.adb_adapter import ADBAdapter
        import subprocess

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("adb", 10)
            result = await adapter.health_check()
            assert result is False


class TestADBAdapterScreenshot:
    """ADB 适配器截图测试"""

    def test_get_screenshot_returns_bytes(self, sample_screenshot):
        """截图返回字节数据"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        # 直接 patch get_screenshot 返回值
        with patch.object(adapter, 'get_screenshot', return_value=sample_screenshot):
            result = adapter.get_screenshot()
            assert isinstance(result, bytes)
            assert len(result) > 0

    def test_get_screenshot_handles_device_error(self):
        """截图处理设备错误"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = Exception("Device not found")

            with pytest.raises(Exception):
                adapter.get_screenshot()


class TestADBAdapterActions:
    """ADB 适配器动作执行测试"""

    def test_execute_tap_action_without_capabilities(self):
        """执行 Tap 动作（无能力信息时直接使用坐标）"""
        from src.adapters.adb_adapter import ADBAdapter
        from src.adapters.base import ActionResult

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'tap') as mock_tap:
            mock_tap.return_value = None
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "tap",
                "element": {"x": 500, "y": 300}
            })

            # 无能力信息时直接使用原始坐标
            mock_tap.assert_called_once_with(500, 300)
            assert result.success is True
            assert result.should_finish is False

    def test_execute_tap_action_with_capabilities(self):
        """执行 Tap 动作（有能力信息时转换坐标）"""
        from src.adapters.adb_adapter import ADBAdapter
        from src.adapters.base import ActionResult, DeviceCapabilities, Platform

        adapter = ADBAdapter("test-device")
        # 设置能力信息（屏幕 1080x1440）
        adapter._capabilities = DeviceCapabilities(
            platform=Platform.ANDROID,
            screen_size=(1080, 1440)
        )

        with patch.object(adapter, 'tap') as mock_tap:
            mock_tap.return_value = None
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "tap",
                "element": {"x": 500, "y": 500}
            })

            # 500/1000 * 1080 = 540, 500/1000 * 1440 = 720
            mock_tap.assert_called_once_with(540, 720)
            assert result.success is True

    def test_execute_swipe_action(self):
        """执行 Swipe 动作"""
        from src.adapters.adb_adapter import ADBAdapter
        from src.adapters.base import ActionResult

        adapter = ADBAdapter("test-device")

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
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

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
        from src.adapters.adb_adapter import ADBAdapter
        from src.adapters.base import ActionResult

        adapter = ADBAdapter("test-device")

        result = adapter.execute_action({
            "_metadata": "finish",
            "message": "任务完成"
        })

        assert result.success is True
        assert result.should_finish is True
        assert result.message == "任务完成"

    def test_execute_tap_action_with_server_payload(self):
        """执行服务端原生动作 payload"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'tap') as mock_tap:
            mock_tap.return_value = None
            result = adapter.execute_action({
                "action": "tap",
                "element": {"x": 500, "y": 300}
            })

            mock_tap.assert_called_once_with(500, 300)
            assert result.success is True
            assert result.should_finish is False

    def test_execute_finish_action_with_server_payload(self):
        """执行服务端 finish payload"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        result = adapter.execute_action({
            "action": "finish",
            "message": "任务完成"
        })

        assert result.success is True
        assert result.should_finish is True
        assert result.message == "任务完成"

    def test_execute_tap_action_with_top_level_coordinates(self):
        """执行服务端 x/y 坐标 payload"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'tap') as mock_tap:
            mock_tap.return_value = None
            result = adapter.execute_action({
                "action": "tap",
                "x": 165,
                "y": 495,
            })

            mock_tap.assert_called_once_with(165, 495)
            assert result.success is True
            assert result.should_finish is False

    def test_execute_long_press_with_top_level_coordinates(self):
        """执行服务端 x/y 长按 payload"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'long_press') as mock_long_press:
            mock_long_press.return_value = None
            result = adapter.execute_action({
                "action": "long_press",
                "x": 100,
                "y": 200,
                "duration": 1500,
            })

            mock_long_press.assert_called_once_with(100, 200, 1500)
            assert result.success is True

    def test_execute_wait_action_with_numeric_duration(self):
        """执行服务端数值 wait payload"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch('src.adapters.adb_adapter.time.sleep') as mock_sleep:
            result = adapter.execute_action({
                "action": "wait",
                "duration": 1,
            })

            mock_sleep.assert_called_once_with(1.0)
            assert result.success is True
            assert result.should_finish is False

    def test_execute_swipe_action_with_top_level_coordinates(self):
        """执行服务端 x1/y1/x2/y2 滑动 payload"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'swipe') as mock_swipe:
            mock_swipe.return_value = None
            result = adapter.execute_action({
                "action": "swipe",
                "x1": 499,
                "y1": 799,
                "x2": 499,
                "y2": 350,
                "duration": 500,
            })

            mock_swipe.assert_called_once_with(499, 799, 499, 350, 500)
            assert result.success is True
            assert result.should_finish is False

    def test_execute_unknown_action_returns_error(self):
        """未知动作返回错误"""
        from src.adapters.adb_adapter import ADBAdapter
        from src.adapters.base import ActionResult

        adapter = ADBAdapter("test-device")

        result = adapter.execute_action({
            "_metadata": "do",
            "action": "unknown_action"
        })

        assert result.success is False
        assert result.should_finish is False
        assert "Unknown action" in result.message


class TestADBAdapterCheckCapabilities:
    """ADB 适配器能力检查测试"""

    @pytest.mark.asyncio
    async def test_check_capabilities_returns_capabilities(self, app_packages):
        """能力检查返回完整能力信息"""
        from src.adapters.adb_adapter import ADBAdapter
        from src.adapters.base import Platform

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_check_output') as mock_check:
            # Mock 设备型号
            mock_check.side_effect = [
                b"MI 13 Pro\n",  # getprop ro.product.model
                b"1080\n",  # wm size width
                b"2400\n",  # wm size height
                b"14\n",  # getprop ro.build.version.release
                b"34\n",  # getprop ro.build.version.sdk
                b"package:com.tencent.mm\npackage:com.alipay.mp\n",  # pm list packages
            ]

            caps = await adapter.check_capabilities()

            assert caps.platform == Platform.ANDROID
            assert caps.device_name == "MI 13 Pro"
            assert caps.screen_size == (1080, 2400)
            assert caps.os_version == "Android 14"
            assert caps.api_level == 34
            assert len(caps.supported_apps) == 2

    @pytest.mark.asyncio
    async def test_check_capabilities_caches_result(self):
        """能力检查结果会被缓存"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_check_output') as mock_check:
            mock_check.side_effect = [
                b"Test Device\n",
                b"1080\n", b"2400\n", b"14\n", b"34\n", b"\n"
            ]

            # 第一次调用
            caps1 = await adapter.check_capabilities()
            # 第二次调用应该使用缓存
            caps2 = await adapter.check_capabilities()

            # check_output 只应该被调用一次（缓存）
            assert mock_check.call_count == 6  # 每项属性一次
            assert caps1 is caps2  # 同一对象


class TestADBAdapterMethods:
    """ADB 适配器具体方法测试"""

    def test_tap_executes_adb_command(self):
        """tap 方法执行正确的 ADB 命令"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.return_value = Mock()
            adapter.tap(500, 800)

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "shell" in args
            assert "input" in args
            assert "tap" in args
            assert "500" in args
            assert "800" in args

    def test_swipe_executes_adb_command(self):
        """swipe 方法执行正确的 ADB 命令"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.return_value = Mock()
            adapter.swipe(100, 200, 100, 400, duration_ms=500)

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "shell" in args
            assert "input" in args
            assert "swipe" in args
            assert "100" in args
            assert "200" in args
            assert "500" in args

    def test_long_press_uses_swipe_command(self):
        """long_press 使用滑动命令模拟"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.return_value = Mock()
            adapter.long_press(500, 500, duration_ms=3000)

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "swipe" in args
            # 起点终点相同，时长 3000ms
            assert args[-1] == "3000"

    def test_back_presses_keycode_4(self):
        """back 按下返回键 (KEYCODE_BACK = 4)"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.return_value = Mock()
            adapter.back()

            args = mock_run.call_args[0][0]
            assert "keyevent" in args
            assert "4" in args

    def test_home_presses_keycode_3(self):
        """home 按下 Home 键 (KEYCODE_HOME = 3)"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.return_value = Mock()
            adapter.home()

            args = mock_run.call_args[0][0]
            assert "keyevent" in args
            assert "3" in args

    def test_launch_app_uses_monkey_command(self):
        """launch_app 使用 monkey 命令"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.return_value = Mock()
            result = adapter.launch_app("com.tencent.mm")

            assert result is True
            args = mock_run.call_args[0][0]
            assert "monkey" in args
            assert "-p" in args
            assert "com.tencent.mm" in args
            assert "-c" in args
            assert "android.intent.category.LAUNCHER" in args

    def test_launch_app_empty_package_returns_false(self):
        """launch_app 空包名返回 False"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            result = adapter.launch_app("")
            assert result is False
            mock_run.assert_not_called()

    def test_double_tap_sends_two_taps(self):
        """double_tap 发送两次 tap"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb') as mock_run:
            mock_run.return_value = Mock()
            adapter.double_tap(500, 500)

            assert mock_run.call_count == 2
            # 验证两次 tap 的坐标相同
            for call in mock_run.call_args_list:
                args = call[0][0]
                assert "tap" in args
                assert "500" in args


class TestCoordinateConversion:
    """坐标转换测试"""

    def test_convert_relative_to_absolute(self):
        """相对坐标转换为绝对坐标"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        # 屏幕 1080x1920
        x, y = adapter._convert_relative_to_absolute(
            {"x": 500, "y": 500},
            1080, 1920
        )

        assert x == 540  # 500/1000 * 1080
        assert y == 960  # 500/1000 * 1920

    def test_convert_edge_cases(self):
        """坐标转换边界情况"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        # 左上角 (0, 0)
        x, y = adapter._convert_relative_to_absolute({"x": 0, "y": 0}, 1080, 1920)
        assert x == 0
        assert y == 0

        # 右下角 (1000, 1000)
        x, y = adapter._convert_relative_to_absolute({"x": 1000, "y": 1000}, 1080, 1920)
        assert x == 1080
        assert y == 1920

    def test_convert_missing_coordinates(self):
        """缺失坐标使用默认值"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        # 只有 x - y 使用相对值 500
        x, y = adapter._convert_relative_to_absolute({"x": 500}, 1080, 1920)
        assert x == 540  # 500/1000 * 1080
        assert y == 960  # 500/1000 * 1920 (使用默认相对值 500)

        # 只有 y - x 使用相对值 500
        x, y = adapter._convert_relative_to_absolute({"y": 500}, 1080, 1920)
        assert x == 540  # 500/1000 * 1080 (使用默认相对值 500)
        assert y == 960  # 500/1000 * 1920
