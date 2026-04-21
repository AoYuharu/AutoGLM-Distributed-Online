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

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            adapter.tap(500, 800)

            mock_run.assert_called_once_with(
                ["shell", "input", "tap", "500", "800"],
                "tap",
            )

    def test_swipe_executes_adb_command(self):
        """swipe 方法执行正确的 ADB 命令"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            adapter.swipe(100, 200, 100, 400, duration_ms=500)

            mock_run.assert_called_once_with(
                ["shell", "input", "swipe", "100", "200", "100", "400", "500"],
                "swipe",
            )

    def test_long_press_uses_swipe_command(self):
        """long_press 使用滑动命令模拟"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            adapter.long_press(500, 500, duration_ms=3000)

            mock_run.assert_called_once_with(
                ["shell", "input", "swipe", "500", "500", "500", "500", "3000"],
                "long_press",
            )


    def test_back_presses_keycode_4(self):
        """back 按下返回键 (KEYCODE_BACK = 4)"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            adapter.back()

            mock_run.assert_called_once_with(
                ["shell", "input", "keyevent", "4"],
                "back",
            )

    def test_home_presses_keycode_3(self):
        """home 按下 Home 键 (KEYCODE_HOME = 3)"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            adapter.home()

            mock_run.assert_called_once_with(
                ["shell", "input", "keyevent", "3"],
                "home",
            )

    def test_launch_app_uses_monkey_command(self):
        """launch_app 使用 monkey 命令"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            result = adapter.launch_app("com.tencent.mm")

            assert result is True
            args = mock_run.call_args[0][0]
            assert "monkey" in args
            assert "-p" in args
            assert "com.tencent.mm" in args
            assert "-c" in args
            assert "android.intent.category.LAUNCHER" in args
            assert mock_run.call_args[0][1] == "launch_app"

    def test_execute_launch_action_resolves_known_app_alias(self):
        """Launch(app=别名) 解析为静态包名"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'launch_app') as mock_launch:
            mock_launch.return_value = True
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "WeChat",
            })

            mock_launch.assert_called_once_with("com.tencent.mm")
            assert result.success is True
            assert result.should_finish is False

    def test_execute_launch_action_unknown_app_alias_returns_not_found(self):
        """Launch(app=未知别名) 返回带提示的 App not found"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "resolve", return_value=None) as mock_resolve, \
             patch.object(adapter._android_app_index, "load_cached", return_value=None) as mock_load_cached, \
             patch.object(adapter._android_app_index, "refresh") as mock_refresh, \
             patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "UnknownAppAlias",
            })

        assert mock_resolve.call_count == 3
        mock_load_cached.assert_called_once_with()
        mock_refresh.assert_called_once_with()
        assert result.success is False
        assert result.should_finish is False
        assert result.message == (
            "App not found: UnknownAppAlias | "
            "No dynamic Android app-name mapping matched this request. "
            "Try a more exact installed app label."
        )

    def test_execute_launch_action_not_found_includes_dynamic_suggestions(self):
        """动态 miss 时返回可供后续 agent 复用的映射提示"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "resolve", return_value=None), \
             patch.object(adapter._android_app_index, "load_cached", return_value=None), \
             patch.object(adapter._android_app_index, "refresh"), \
             patch.object(
                 adapter._android_app_index,
                 "get_package_suggestions",
                 return_value=[("com.android.notes", ["原子笔记"]), ("com.tencent.mm", ["微信", "WeChat"])],
             ):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "MissingAlias",
            })

        assert result.success is False
        assert result.message == (
            "App not found: MissingAlias | "
            "Available app suggestions: 原子笔记 -> com.android.notes; 微信, WeChat -> com.tencent.mm"
        )

    def test_execute_launch_action_failed_package_includes_known_aliases(self):
        """启动失败时返回包名及已知别名提示"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'launch_app', return_value=False), \
             patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "原子笔记",
            })

        assert result.success is False
        assert result.message == "App not found: 原子笔记 | Known aliases for com.android.notes: 原子笔记"

    def test_execute_launch_action_new_static_alias_resolves_notes(self):
        """原子笔记静态别名直接命中 com.android.notes"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'launch_app', return_value=True) as mock_launch, \
             patch.object(adapter._android_app_index, "resolve") as mock_resolve:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "原子笔记",
            })

        mock_resolve.assert_not_called()
        mock_launch.assert_called_once_with("com.android.notes")
        assert result.success is True

    def test_execute_launch_action_dynamic_failure_message_uses_package_when_app_absent(self):
        """仅 package 启动失败时提示包名"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'launch_app', return_value=False), \
             patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "package": "com.example.missing",
            })

        assert result.success is False
        assert result.message == "App not found: com.example.missing"

    def test_execute_launch_action_dynamic_failure_message_deduplicates_aliases(self):
        """别名提示去重，避免 observe 文本重复"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.tencent.mm", ["微信", "微信", "WeChat"])],
        ):
            message = adapter._build_launch_failure_message(app_name="MissingAlias")

        assert message == (
            "App not found: MissingAlias | "
            "Available app suggestions: 微信, WeChat -> com.tencent.mm"
        )

    def test_execute_launch_action_dynamic_failure_message_deduplicates_known_aliases(self):
        """已知包别名提示去重"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="WeChat", package="com.tencent.mm")

        assert message == "App not found: WeChat | Known aliases for com.tencent.mm: 微信, WeChat, wechat"

    def test_execute_launch_action_dynamic_failure_message_unknown_target_defaults(self):
        """无 app/package 时默认 unknown"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message()

        assert message == "App not found: unknown"

    def test_execute_launch_action_dynamic_failure_message_prefers_app_name_target(self):
        """app 与 package 同时存在时优先用 app 作为失败目标"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="原子笔记", package="com.android.notes")

        assert message.startswith("App not found: 原子笔记")

    def test_execute_launch_action_dynamic_failure_message_with_package_only_suggestions(self):
        """无标签建议时仅输出包名"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        formatted = adapter._format_launch_suggestions([("com.example.app", [])])

        assert formatted == "com.example.app"

    def test_execute_launch_action_dynamic_failure_message_with_multiple_labels_formats_once(self):
        """多个标签建议按固定格式拼接"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        formatted = adapter._format_launch_suggestions([
            ("com.example.one", ["One"]),
            ("com.example.two", ["Two", "Deux"]),
        ])

        assert formatted == "One -> com.example.one; Two, Deux -> com.example.two"

    def test_execute_launch_action_dynamic_failure_message_static_alias_without_suggestions(self):
        """静态别名失败时仍返回已知别名提示"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="原子笔记", package="com.android.notes")

        assert message == "App not found: 原子笔记 | Known aliases for com.android.notes: 原子笔记"

    def test_execute_launch_action_dynamic_failure_message_no_duplicate_parts(self):
        """无建议且无别名时只追加一次 fallback 文案"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="MissingAlias")

        assert message.count("No dynamic Android app-name mapping matched this request") == 1

    def test_execute_launch_action_dynamic_failure_message_includes_aliases_and_suggestions(self):
        """别名和建议可同时出现在失败消息中"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.tencent.mm", ["微信", "WeChat"])],
        ):
            message = adapter._build_launch_failure_message(app_name="WeChat", package="com.tencent.mm")

        assert message == (
            "App not found: WeChat | "
            "Known aliases for com.tencent.mm: 微信, WeChat, wechat | "
            "Available app suggestions: 微信, WeChat -> com.tencent.mm"
        )

    def test_execute_launch_action_dynamic_failure_message_returns_plain_package_when_no_app(self):
        """无 app 时失败目标使用 package"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(package="com.example.pkg")

        assert message == "App not found: com.example.pkg"

    def test_execute_launch_action_dynamic_failure_message_omits_fallback_when_suggestions_exist(self):
        """有建议时不再追加泛化 fallback 文案"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.android.notes", ["原子笔记"])],
        ):
            message = adapter._build_launch_failure_message(app_name="MissingAlias")

        assert "No dynamic Android app-name mapping matched this request" not in message

    def test_execute_launch_action_dynamic_failure_message_omits_fallback_when_alias_hints_exist(self):
        """有包别名时不追加泛化 fallback 文案"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="原子笔记", package="com.android.notes")

        assert "No dynamic Android app-name mapping matched this request" not in message

    def test_execute_launch_action_dynamic_failure_message_with_app_only_no_suggestions(self):
        """仅 app 且无建议时包含 fallback 文案"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="NotInstalled")

        assert message == (
            "App not found: NotInstalled | "
            "No dynamic Android app-name mapping matched this request. "
            "Try a more exact installed app label."
        )

    def test_execute_launch_action_dynamic_failure_message_with_package_alias_and_suggestions_formats_all(self):
        """复杂失败消息格式保持稳定"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.android.notes", ["原子笔记"]), ("com.tencent.mm", ["微信"])],
        ):
            message = adapter._build_launch_failure_message(app_name="原子笔记", package="com.android.notes")

        assert message == (
            "App not found: 原子笔记 | "
            "Known aliases for com.android.notes: 原子笔记 | "
            "Available app suggestions: 原子笔记 -> com.android.notes; 微信 -> com.tencent.mm"
        )

    def test_execute_launch_action_dynamic_failure_message_empty_suggestion_labels_use_package_only(self):
        """建议标签为空时只显示包名"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.empty.labels", [])],
        ):
            message = adapter._build_launch_failure_message(app_name="MissingAlias")

        assert message == "App not found: MissingAlias | Available app suggestions: com.empty.labels"

    def test_execute_launch_action_dynamic_failure_message_preserves_order(self):
        """失败消息顺序固定为 target -> aliases -> suggestions"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.android.notes", ["原子笔记"])],
        ):
            message = adapter._build_launch_failure_message(app_name="原子笔记", package="com.android.notes")

        assert message.split(" | ") == [
            "App not found: 原子笔记",
            "Known aliases for com.android.notes: 原子笔记",
            "Available app suggestions: 原子笔记 -> com.android.notes",
        ]

    def test_execute_launch_action_dynamic_failure_message_without_package_and_without_app_returns_unknown(self):
        """无目标时返回 unknown"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            assert adapter._build_launch_failure_message() == "App not found: unknown"

    def test_execute_launch_action_dynamic_failure_message_returns_aliases_for_wechat_package(self):
        """微信包返回完整静态别名列表"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(package="com.tencent.mm")

        assert message == "App not found: com.tencent.mm | Known aliases for com.tencent.mm: 微信, WeChat, wechat"

    def test_execute_launch_action_dynamic_failure_message_for_static_only_alias_target(self):
        """静态别名目标保留用户原始 app 名称"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="WeChat", package="com.tencent.mm")

        assert message.startswith("App not found: WeChat")

    def test_execute_launch_action_dynamic_failure_message_no_suggestions_for_package_only(self):
        """仅 package 且无建议时不包含 fallback 文案"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(package="com.example.pkg")

        assert "No dynamic Android app-name mapping matched this request" not in message

    def test_execute_launch_action_dynamic_failure_message_aliases_follow_package(self):
        """别名提示绑定到 package"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="Alias", package="com.tencent.mm")

        assert "Known aliases for com.tencent.mm" in message

    def test_execute_launch_action_dynamic_failure_message_suggestions_follow_helper_output(self):
        """建议提示直接使用 helper 输出"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.pkg.one", ["One"]), ("com.pkg.two", ["Two"])],
        ):
            message = adapter._build_launch_failure_message(app_name="Missing")

        assert message.endswith("Available app suggestions: One -> com.pkg.one; Two -> com.pkg.two")

    def test_execute_launch_action_dynamic_failure_message_app_not_found_prefix_stable(self):
        """失败消息前缀保持稳定"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="Missing")

        assert message.startswith("App not found: Missing")

    def test_execute_launch_action_dynamic_failure_message_for_unknown_without_suggestions_has_two_parts(self):
        """未知 app 无建议时固定两段"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            parts = adapter._build_launch_failure_message(app_name="Missing").split(" | ")

        assert len(parts) == 2

    def test_execute_launch_action_dynamic_failure_message_for_package_only_with_aliases_has_two_parts(self):
        """仅 package 且有别名时固定两段"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            parts = adapter._build_launch_failure_message(package="com.tencent.mm").split(" | ")

        assert len(parts) == 2

    def test_execute_launch_action_dynamic_failure_message_for_package_alias_and_suggestion_has_three_parts(self):
        """别名+建议时固定三段"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.android.notes", ["原子笔记"])],
        ):
            parts = adapter._build_launch_failure_message(package="com.tencent.mm").split(" | ")

        assert len(parts) == 3

    def test_execute_launch_action_dynamic_failure_message_without_aliases_uses_suggestions_only(self):
        """无别名时只有建议段"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.android.notes", ["原子笔记"])],
        ):
            message = adapter._build_launch_failure_message(app_name="Missing")

        assert message == "App not found: Missing | Available app suggestions: 原子笔记 -> com.android.notes"

    def test_execute_launch_action_dynamic_failure_message_without_anything_is_single_part(self):
        """unknown 无附加信息时只有一段"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            parts = adapter._build_launch_failure_message().split(" | ")

        assert parts == ["App not found: unknown"]

    def test_execute_launch_action_dynamic_failure_message_known_aliases_for_notes_remain_single(self):
        """原子笔记别名保持单值"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            assert adapter._build_launch_failure_message(package="com.android.notes") == (
                "App not found: com.android.notes | Known aliases for com.android.notes: 原子笔记"
            )

    def test_execute_launch_action_dynamic_failure_message_wechat_aliases_are_joined(self):
        """微信多个静态别名按逗号拼接"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            assert ", " in adapter._build_launch_failure_message(package="com.tencent.mm")

    def test_execute_launch_action_dynamic_failure_message_helper_format_is_used_for_empty_labels(self):
        """helper 格式方法处理空标签"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        assert adapter._format_launch_suggestions([("pkg", [])]) == "pkg"

    def test_execute_launch_action_dynamic_failure_message_helper_format_deduplicates_labels(self):
        """helper 格式方法去重标签"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        assert adapter._format_launch_suggestions([("pkg", ["A", "A", "B"])]) == "A, B -> pkg"

    def test_execute_launch_action_dynamic_failure_message_for_unknown_app_uses_fallback_text(self):
        """未知 app 失败文案包含 fallback 提示"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            assert adapter._build_launch_failure_message(app_name="Unknown") == (
                "App not found: Unknown | "
                "No dynamic Android app-name mapping matched this request. "
                "Try a more exact installed app label."
            )

    def test_execute_launch_action_dynamic_failure_message_for_static_alias_failure_has_no_fallback_text(self):
        """静态别名失败无泛化 fallback 文案"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(app_name="WeChat", package="com.tencent.mm")

        assert "No dynamic Android app-name mapping matched this request" not in message

    def test_execute_launch_action_dynamic_failure_message_suggestions_can_include_same_package(self):
        """建议中允许包含同一包"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.tencent.mm", ["微信"])],
        ):
            message = adapter._build_launch_failure_message(package="com.tencent.mm")

        assert message.endswith("Available app suggestions: 微信 -> com.tencent.mm")

    def test_execute_launch_action_dynamic_failure_message_empty_aliases_and_empty_suggestions_for_package_only(self):
        """仅 package 且无别名无建议时仅目标"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            message = adapter._build_launch_failure_message(package="com.example.pkg")

        assert message == "App not found: com.example.pkg"

    def test_execute_launch_action_dynamic_failure_message_empty_aliases_but_suggestions_for_package_only(self):
        """仅 package 且有建议时显示建议"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.android.notes", ["原子笔记"])],
        ):
            message = adapter._build_launch_failure_message(package="com.example.pkg")

        assert message == "App not found: com.example.pkg | Available app suggestions: 原子笔记 -> com.android.notes"

    def test_execute_launch_action_dynamic_failure_message_can_be_used_for_observe_result_text(self):
        """失败消息适合直接进入 observe_result.result"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.android.notes", ["原子笔记"]), ("com.tencent.mm", ["微信", "WeChat"])],
        ):
            message = adapter._build_launch_failure_message(app_name="MissingAlias")

        assert "|" in message
        assert "Available app suggestions:" in message

    def test_execute_launch_action_dynamic_failure_message_target_unknown_when_none(self):
        """目标缺失时 target 为 unknown"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            assert adapter._build_launch_failure_message() == "App not found: unknown"

    def test_execute_launch_action_dynamic_failure_message_with_notes_aliases_and_suggestions(self):
        """原子笔记失败消息包含别名与建议"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.android.notes", ["原子笔记"])],
        ):
            message = adapter._build_launch_failure_message(app_name="原子笔记", package="com.android.notes")

        assert message == (
            "App not found: 原子笔记 | "
            "Known aliases for com.android.notes: 原子笔记 | "
            "Available app suggestions: 原子笔记 -> com.android.notes"
        )

    def test_execute_launch_action_dynamic_failure_message_returns_string(self):
        """失败消息始终返回字符串"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            assert isinstance(adapter._build_launch_failure_message(app_name="Missing"), str)

    def test_execute_launch_action_dynamic_failure_message_can_handle_empty_label_entries(self):
        """空标签项会被过滤"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        assert adapter._format_launch_suggestions([("pkg", ["", "A", "", "B"])]) == "A, B -> pkg"

    def test_execute_launch_action_dynamic_failure_message_for_missing_alias_with_suggestions(self):
        """缺失别名但有建议时直接输出建议"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "get_package_suggestions",
            return_value=[("com.android.notes", ["原子笔记"]), ("com.tencent.mm", ["微信"])],
        ):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "MissingAlias",
            })

        assert result.message == (
            "App not found: MissingAlias | "
            "Available app suggestions: 原子笔记 -> com.android.notes; 微信 -> com.tencent.mm"
        )

    def test_execute_launch_action_dynamic_failure_message_static_failure_observe_safe(self):
        """静态失败文案可以直接给 observe 使用"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, "launch_app", return_value=False), \
             patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "WeChat",
            })

        assert result.message.startswith("App not found: WeChat")
        assert "Known aliases for com.tencent.mm" in result.message

    def test_execute_launch_action_uses_dynamic_cache_hit_without_refresh(self):
        """静态 miss 但动态内存缓存命中时直接启动"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "resolve", return_value="com.android.notes") as mock_resolve, \
             patch.object(adapter._android_app_index, "load_cached") as mock_load_cached, \
             patch.object(adapter._android_app_index, "refresh") as mock_refresh, \
             patch.object(adapter, "launch_app", return_value=True) as mock_launch:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "OriginalNotesApp",
            })

        mock_resolve.assert_called_once_with("OriginalNotesApp")
        mock_load_cached.assert_not_called()
        mock_refresh.assert_not_called()
        mock_launch.assert_called_once_with("com.android.notes")
        assert result.success is True

    def test_execute_launch_action_refreshes_after_disk_cache_miss(self):
        """静态 miss 且缓存 miss 时会触发 refresh"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter._android_app_index,
            "resolve",
            side_effect=[None, None, "com.android.notes"],
        ) as mock_resolve, patch.object(
            adapter._android_app_index,
            "load_cached",
            return_value=None,
        ) as mock_load_cached, patch.object(
            adapter._android_app_index,
            "refresh",
        ) as mock_refresh, patch.object(
            adapter,
            "launch_app",
            return_value=True,
        ) as mock_launch:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "OriginalNotesApp",
            })

        assert mock_resolve.call_count == 3
        mock_load_cached.assert_called_once_with()
        mock_refresh.assert_called_once_with()
        mock_launch.assert_called_once_with("com.android.notes")
        assert result.success is True

    def test_execute_launch_action_retries_once_after_dynamic_launch_failure(self):
        """动态命中后启动失败会失效缓存并重试一次"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "resolve", side_effect=["com.android.notes", "com.android.notes.new"]) as mock_resolve, \
             patch.object(adapter._android_app_index, "invalidate") as mock_invalidate, \
             patch.object(adapter._android_app_index, "refresh") as mock_refresh, \
             patch.object(adapter, "launch_app", side_effect=[False, True]) as mock_launch:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "OriginalNotesApp",
            })

        mock_invalidate.assert_called_once_with("com.android.notes")
        mock_refresh.assert_called_once_with()
        assert mock_launch.call_args_list[0].args == ("com.android.notes",)
        assert mock_launch.call_args_list[1].args == ("com.android.notes.new",)
        assert result.success is True

    def test_execute_launch_action_retries_once_after_dynamic_launch_exception(self):
        """动态命中后抛异常也会做一次失效刷新重试"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "resolve", side_effect=["com.android.notes", "com.android.notes"]) as mock_resolve, \
             patch.object(adapter._android_app_index, "invalidate") as mock_invalidate, \
             patch.object(adapter._android_app_index, "refresh") as mock_refresh, \
             patch.object(adapter, "launch_app", side_effect=[RuntimeError("launch_app: failed"), True]) as mock_launch:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "OriginalNotesApp",
            })

        mock_invalidate.assert_called_once_with("com.android.notes")
        mock_refresh.assert_called_once_with()
        assert mock_resolve.call_count == 2
        assert mock_launch.call_args_list[0].args == ("com.android.notes",)
        assert mock_launch.call_args_list[1].args == ("com.android.notes",)
        assert result.success is True

    def test_execute_launch_action_dynamic_retry_returns_not_found_when_refresh_cannot_resolve(self):
        """动态重试刷新后仍无法解析则返回包名 not found"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "resolve", side_effect=["com.android.notes", None]) as mock_resolve, \
             patch.object(adapter._android_app_index, "invalidate") as mock_invalidate, \
             patch.object(adapter._android_app_index, "refresh") as mock_refresh, \
             patch.object(adapter, "launch_app", return_value=False) as mock_launch, \
             patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "OriginalNotesApp",
            })

        mock_invalidate.assert_called_once_with("com.android.notes")
        mock_refresh.assert_called_once_with()
        mock_launch.assert_called_once_with("com.android.notes")
        assert result.success is False
        assert result.message == "App not found: OriginalNotesApp | Known aliases for com.android.notes: 原子笔记"

    def test_execute_launch_action_static_hit_does_not_use_dynamic_index(self):
        """静态命中保持第一优先级，不触发动态索引"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "resolve") as mock_resolve, \
             patch.object(adapter._android_app_index, "load_cached") as mock_load_cached, \
             patch.object(adapter._android_app_index, "refresh") as mock_refresh, \
             patch.object(adapter, "launch_app", return_value=True) as mock_launch:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "WeChat",
            })

        mock_resolve.assert_not_called()
        mock_load_cached.assert_not_called()
        mock_refresh.assert_not_called()
        mock_launch.assert_called_once_with("com.tencent.mm")
        assert result.success is True

    def test_execute_launch_action_app_still_does_not_fallback_to_package_when_dynamic_misses(self):
        """有 app 时动态 miss 后仍不回退 package"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "resolve", return_value=None), \
             patch.object(adapter._android_app_index, "load_cached", return_value=None), \
             patch.object(adapter._android_app_index, "refresh"), \
             patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "MissingAlias",
                "package": "com.tencent.mm",
            })

        assert result.success is False
        assert result.message == (
            "App not found: MissingAlias | "
            "No dynamic Android app-name mapping matched this request. "
            "Try a more exact installed app label."
        )

    def test_execute_launch_action_dynamic_ambiguity_returns_not_found(self):
        """动态歧义时不自动选择包名"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "resolve", return_value=None), \
             patch.object(adapter._android_app_index, "load_cached", return_value=None), \
             patch.object(adapter._android_app_index, "refresh") as mock_refresh, \
             patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]), \
             patch.object(adapter, "launch_app") as mock_launch:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "Notes",
            })

        mock_refresh.assert_called_once_with()
        mock_launch.assert_not_called()
        assert result.success is False
        assert result.message == (
            "App not found: Notes | "
            "No dynamic Android app-name mapping matched this request. "
            "Try a more exact installed app label."
        )

    def test_execute_launch_action_prefers_app_over_package(self):
        """有 app 时不回退使用 package"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter._android_app_index, "get_package_suggestions", return_value=[]):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "app": "MissingAlias",
                "package": "com.tencent.mm",
            })

        assert result.success is False
        assert result.message == (
            "App not found: MissingAlias | "
            "No dynamic Android app-name mapping matched this request. "
            "Try a more exact installed app label."
        )

    def test_execute_launch_action_uses_package_when_app_absent(self):
        """app 缺失时回退使用 package"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, 'launch_app', return_value=True) as mock_launch:
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "launch",
                "package": "com.example.app",
            })

        mock_launch.assert_called_once_with("com.example.app")
        assert result.success is True

    def test_execute_tap_action_returns_failure_when_adb_command_fails(self):
        """ADB 非零返回会让 execute_action 失败"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb', return_value=Mock(returncode=1, stderr='device offline', stdout='')):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "tap",
                "element": {"x": 100, "y": 200},
            })

        assert result.success is False
        assert result.should_finish is False
        assert result.message == "Action failed: tap: device offline"

    def test_execute_type_action_returns_failure_when_adb_keyboard_broadcast_fails(self):
        """ADBKeyboard 广播失败会通过 execute_action 返回错误"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_ensure_adb_keyboard_ready', return_value="com.example.ime/.ExampleIME"), \
             patch.object(adapter, '_type_via_adb_keyboard', side_effect=RuntimeError('type_text(broadcast): receiver unavailable')):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "type",
                "text": "hello world",
            })

        assert result.success is False
        assert result.message == "Action failed: type_text(broadcast): receiver unavailable"

    def test_run_adb_checked_detects_failure_text_even_with_zero_exit(self):
        """明显 adb 错误文本也会被视为失败"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb', return_value=Mock(returncode=0, stderr='Error: no devices/emulators found', stdout='')):
            with pytest.raises(RuntimeError, match="type_text: Error: no devices/emulators found"):
                adapter._run_adb_checked(["shell", "input", "text", "hello"], action_name="type_text")

    def test_double_tap_sends_two_taps(self):
        """double_tap 发送两次 tap"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            adapter.double_tap(500, 500)

            assert mock_run.call_count == 2
            for call in mock_run.call_args_list:
                args = call[0][0]
                assert "tap" in args
                assert "500" in args
                assert call.kwargs["action_name"] == "double_tap"

    def test_tap_uses_checked_adb_command(self):
        """tap 使用 checked adb helper"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            adapter.tap(10, 20)

            mock_run.assert_called_once_with(
                ["shell", "input", "tap", "10", "20"],
                "tap",
            )

    def test_swipe_uses_checked_adb_command(self):
        """swipe 使用 checked adb helper"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            adapter.swipe(1, 2, 3, 4, duration_ms=500)

            mock_run.assert_called_once_with(
                ["shell", "input", "swipe", "1", "2", "3", "4", "500"],
                "swipe",
            )

    def test_long_press_uses_checked_adb_command(self):
        """long_press 使用 checked adb helper"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            adapter.long_press(1, 2, duration_ms=700)

            mock_run.assert_called_once_with(
                ["shell", "input", "swipe", "1", "2", "1", "2", "700"],
                "long_press",
            )

    def test_get_adb_keyboard_status_distinguishes_installed_enabled_and_active(self):
        """状态读取能区分 installed/enabled/active"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(
            adapter,
            '_run_adb',
            return_value=Mock(returncode=0, stdout="mId=com.android.adbkeyboard/.AdbIME\n", stderr=""),
        ), patch.object(adapter, '_run_adb_checked') as mock_checked:
            mock_checked.side_effect = [
                Mock(returncode=0, stdout="com.example.ime/.ExampleIME:com.android.adbkeyboard/.AdbIME\n", stderr=""),
                Mock(returncode=0, stdout="com.android.adbkeyboard/.AdbIME\n", stderr=""),
            ]
            status = adapter._get_adb_keyboard_status()

        assert status.installed is True
        assert status.enabled is True
        assert status.active is True
        assert status.current_ime == "com.android.adbkeyboard/.AdbIME"
        assert adapter._adb_keyboard_available is True

    def test_type_text_fails_when_adb_keyboard_is_not_installed(self):
        """ADBKeyboard 未安装时 type_text 直接失败"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_ensure_adb_keyboard_ready', side_effect=RuntimeError(
            "ADB Keyboard (com.android.adbkeyboard/.AdbIME) is not installed on the device"
        )), patch.object(adapter, '_type_via_adb_keyboard') as mock_type_via_adb_keyboard:
            with pytest.raises(RuntimeError, match="ADB Keyboard \(com\.android\.adbkeyboard/\.AdbIME\) is not installed on the device"):
                adapter.type_text("hello")

        mock_type_via_adb_keyboard.assert_not_called()

    def test_type_text_tries_enable_when_adb_keyboard_is_installed_but_not_enabled(self):
        """已安装未启用时会先尝试 ime enable"""
        from src.adapters.adb_adapter import ADBAdapter, ADBKeyboardStatus

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_get_adb_keyboard_status') as mock_status, \
             patch.object(adapter, '_run_adb_checked') as mock_run, \
             patch.object(adapter, '_sleep_after_action') as mock_sleep:
            mock_status.side_effect = [
                ADBKeyboardStatus(installed=True, enabled=False, active=False, current_ime="com.example.ime/.ExampleIME"),
                ADBKeyboardStatus(installed=True, enabled=True, active=True, current_ime="com.android.adbkeyboard/.AdbIME"),
            ]
            original_ime = adapter._ensure_adb_keyboard_ready()

        assert original_ime == "com.example.ime/.ExampleIME"
        assert mock_run.call_args_list[0].args[0] == ["shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME"]
        assert mock_run.call_args_list[0].kwargs["action_name"] == "type_text(enable_ime)"
        mock_sleep.assert_called_once()

    def test_type_text_returns_installed_but_not_enabled_when_enable_does_not_make_ime_ready(self):
        """启用失败后返回 installed but not enabled 诊断"""
        from src.adapters.adb_adapter import ADBAdapter, ADBKeyboardStatus

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_get_adb_keyboard_status') as mock_status, \
             patch.object(adapter, '_run_adb_checked', side_effect=RuntimeError('type_text(enable_ime): Unknown input method')) as mock_run:
            mock_status.side_effect = [
                ADBKeyboardStatus(installed=True, enabled=False, active=False, current_ime="com.example.ime/.ExampleIME"),
                ADBKeyboardStatus(installed=True, enabled=False, active=False, current_ime="com.example.ime/.ExampleIME"),
            ]
            with pytest.raises(RuntimeError, match="ADB Keyboard \(com\.android\.adbkeyboard/\.AdbIME\) is installed but not enabled on the device"):
                adapter._ensure_adb_keyboard_ready()

        assert mock_run.call_args_list[0].args[0] == ["shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME"]

    def test_type_text_tries_set_when_adb_keyboard_is_enabled_but_not_active(self):
        """已启用未激活时会执行 ime set"""
        from src.adapters.adb_adapter import ADBAdapter, ADBKeyboardStatus

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_get_adb_keyboard_status') as mock_status, \
             patch.object(adapter, '_run_adb_checked') as mock_run, \
             patch.object(adapter, '_sleep_after_action') as mock_sleep:
            mock_status.side_effect = [
                ADBKeyboardStatus(installed=True, enabled=True, active=False, current_ime="com.example.ime/.ExampleIME"),
                ADBKeyboardStatus(installed=True, enabled=True, active=True, current_ime="com.android.adbkeyboard/.AdbIME"),
            ]
            original_ime = adapter._ensure_adb_keyboard_ready()

        assert original_ime == "com.example.ime/.ExampleIME"
        assert mock_run.call_args_list[0].args[0] == ["shell", "ime", "set", "com.android.adbkeyboard/.AdbIME"]
        assert mock_run.call_args_list[0].kwargs["action_name"] == "type_text(set_ime)"
        mock_sleep.assert_called_once()

    def test_type_text_returns_enabled_but_could_not_be_activated_when_set_does_not_activate_ime(self):
        """已启用但激活失败时返回更精确错误"""
        from src.adapters.adb_adapter import ADBAdapter, ADBKeyboardStatus

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_get_adb_keyboard_status') as mock_status, \
             patch.object(adapter, '_run_adb_checked', side_effect=RuntimeError('type_text(set_ime): Unknown input method')) as mock_run:
            mock_status.side_effect = [
                ADBKeyboardStatus(installed=True, enabled=True, active=False, current_ime="com.example.ime/.ExampleIME"),
                ADBKeyboardStatus(installed=True, enabled=True, active=False, current_ime="com.example.ime/.ExampleIME"),
            ]
            with pytest.raises(RuntimeError, match="ADB Keyboard \(com\.android\.adbkeyboard/\.AdbIME\) is enabled but could not be activated; current IME is com\.example\.ime/\.ExampleIME"):
                adapter._ensure_adb_keyboard_ready()

        assert mock_run.call_args_list[0].args[0] == ["shell", "ime", "set", "com.android.adbkeyboard/.AdbIME"]

    def test_type_text_regression_installed_but_not_enabled_enables_before_any_set_attempt(self):
        """回归：已安装未启用时先 enable，再决定是否 set"""
        from src.adapters.adb_adapter import ADBAdapter, ADBKeyboardStatus

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_get_adb_keyboard_status') as mock_status, \
             patch.object(adapter, '_run_adb_checked') as mock_run, \
             patch.object(adapter, '_sleep_after_action'):
            mock_status.side_effect = [
                ADBKeyboardStatus(installed=True, enabled=False, active=False, current_ime="com.example.ime/.ExampleIME"),
                ADBKeyboardStatus(installed=True, enabled=True, active=False, current_ime="com.example.ime/.ExampleIME"),
                ADBKeyboardStatus(installed=True, enabled=True, active=True, current_ime="com.android.adbkeyboard/.AdbIME"),
            ]
            adapter._ensure_adb_keyboard_ready()

        action_names = [call.kwargs["action_name"] for call in mock_run.call_args_list]
        assert action_names == ["type_text(enable_ime)", "type_text(set_ime)"]

    def test_type_text_uses_adb_keyboard_base64_broadcast_only(self):
        """成功输入只走 ADBKeyboard base64 广播和可选恢复"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked', return_value=Mock(returncode=0, stdout="", stderr="")) as mock_run, \
             patch.object(adapter, '_sleep_after_action') as mock_sleep:
            adapter._type_via_adb_keyboard("你好 world! @#$", "com.example.ime/.ExampleIME")

        calls = mock_run.call_args_list
        broadcast_args = calls[0].args[0]
        assert broadcast_args[:6] == ["shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es"]
        assert broadcast_args[6] == "msg"
        assert broadcast_args[7] == "5L2g5aW9IHdvcmxkISBAIyQ="
        assert calls[0].kwargs["action_name"] == "type_text(broadcast)"
        assert calls[1].args[0] == ["shell", "ime", "set", "com.example.ime/.ExampleIME"]
        assert calls[1].kwargs["action_name"] == "type_text(restore_ime)"
        assert len(calls) == 2
        assert mock_sleep.call_count == 2

    def test_type_text_does_not_restore_when_adb_keyboard_was_already_active(self):
        """当前 IME 已是 ADBKeyboard 时只广播不恢复"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked', return_value=Mock(returncode=0, stdout="", stderr="")) as mock_run, \
             patch.object(adapter, '_sleep_after_action') as mock_sleep:
            adapter._type_via_adb_keyboard("abc", "com.android.adbkeyboard/.AdbIME")

        calls = mock_run.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["action_name"] == "type_text(broadcast)"
        assert calls[0].args[0][4] == "ADB_INPUT_B64"
        assert mock_sleep.call_count == 1

    def test_type_text_restore_ime_failure_is_warning_only(self):
        """恢复原输入法失败只记 warning，不让输入失败"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        def _checked_side_effect(args, action_name, **kwargs):
            if action_name == "type_text(restore_ime)":
                raise RuntimeError("type_text(restore_ime): restore failed")
            return Mock(returncode=0, stdout="", stderr="")

        with patch.object(adapter, '_run_adb_checked', side_effect=_checked_side_effect) as mock_run, \
             patch.object(adapter, '_sleep_after_action') as mock_sleep, \
             patch.object(adapter, '_log') as mock_log:
            adapter._type_via_adb_keyboard("hello", "com.example.ime/.ExampleIME")

        warning_messages = [call.args[1] for call in mock_log.call_args_list if call.args and call.args[0] == "warning"]
        assert any("Failed to restore original IME" in message for message in warning_messages)
        assert any(call.kwargs.get("action_name") == "type_text(restore_ime)" for call in mock_run.call_args_list)
        assert mock_sleep.call_count == 1

    def test_type_text_propagates_broadcast_failure(self):
        """广播失败时直接抛错"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        def _checked_side_effect(args, action_name, **kwargs):
            if action_name == "type_text(broadcast)":
                raise RuntimeError("type_text(broadcast): broadcast failed")
            return Mock(returncode=0, stdout="", stderr="")

        with patch.object(adapter, '_run_adb_checked', side_effect=_checked_side_effect):
            with pytest.raises(RuntimeError, match="type_text\(broadcast\): broadcast failed"):
                adapter._type_via_adb_keyboard("hello", "com.example.ime/.ExampleIME")

    def test_type_text_uses_readiness_then_adb_keyboard_only(self):
        """type_text 入口先做 readiness，再调用单一路径"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_ensure_adb_keyboard_ready', return_value="com.example.ime/.ExampleIME") as mock_ready, \
             patch.object(adapter, '_type_via_adb_keyboard') as mock_type_via_adb_keyboard, \
             patch.object(adapter, '_run_adb_checked') as mock_run:
            adapter.type_text("hello")

        mock_ready.assert_called_once_with()
        mock_type_via_adb_keyboard.assert_called_once_with("hello", "com.example.ime/.ExampleIME")
        mock_run.assert_not_called()

    def test_type_text_returns_immediately_for_empty_text(self):
        """空文本直接返回"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_ensure_adb_keyboard_ready') as mock_ready, \
             patch.object(adapter, '_type_via_adb_keyboard') as mock_type_via_adb_keyboard:
            adapter.type_text("")

        mock_ready.assert_not_called()
        mock_type_via_adb_keyboard.assert_not_called()

    def test_execute_type_action_returns_failure_when_adb_keyboard_is_missing(self):
        """ADBKeyboard 未安装时 execute_action 返回失败结果"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_ensure_adb_keyboard_ready', side_effect=RuntimeError(
            "ADB Keyboard (com.android.adbkeyboard/.AdbIME) is not installed on the device"
        )):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "type",
                "text": "hello world",
            })

        assert result.success is False
        assert result.should_finish is False
        assert result.message == (
            "Action failed: ADB Keyboard (com.android.adbkeyboard/.AdbIME) is not installed on the device"
        )

    def test_execute_type_action_returns_failure_when_adb_keyboard_is_installed_but_not_enabled(self):
        """readiness 诊断会进入 execute_action 结果"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_ensure_adb_keyboard_ready', side_effect=RuntimeError(
            "ADB Keyboard (com.android.adbkeyboard/.AdbIME) is installed but not enabled on the device"
        )):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "type",
                "text": "hello world",
            })

        assert result.success is False
        assert result.message == (
            "Action failed: ADB Keyboard (com.android.adbkeyboard/.AdbIME) is installed but not enabled on the device"
        )

    def test_execute_type_action_returns_failure_when_adb_keyboard_could_not_be_activated(self):
        """激活失败诊断会进入 execute_action 结果"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_ensure_adb_keyboard_ready', side_effect=RuntimeError(
            "ADB Keyboard (com.android.adbkeyboard/.AdbIME) is enabled but could not be activated; current IME is com.example.ime/.ExampleIME"
        )):
            result = adapter.execute_action({
                "_metadata": "do",
                "action": "type",
                "text": "hello world",
            })

        assert result.success is False
        assert result.message == (
            "Action failed: ADB Keyboard (com.android.adbkeyboard/.AdbIME) is enabled but could not be activated; current IME is com.example.ime/.ExampleIME"
        )

    def test_launch_app_empty_package_returns_false(self):
        """launch_app 空包名返回 False"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            result = adapter.launch_app("")
            assert result is False
            mock_run.assert_not_called()

    def test_launch_app_empty_package_returns_false(self):
        """launch_app 空包名返回 False"""
        from src.adapters.adb_adapter import ADBAdapter

        adapter = ADBAdapter("test-device")

        with patch.object(adapter, '_run_adb_checked') as mock_run:
            result = adapter.launch_app("")
            assert result is False
            mock_run.assert_not_called()


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
