"""
Polling module 单元测试 - TDD 风格
"""
import pytest
import time
from unittest.mock import Mock, patch, MagicMock


class TestPlatformType:
    """平台类型枚举测试"""

    def test_platform_types(self):
        """测试平台类型定义"""
        from src.polling.factory import PlatformType

        assert PlatformType.ADB.value == "adb"
        assert PlatformType.HDC.value == "hdc"
        assert PlatformType.WDA.value == "wda"


class TestBasePolling:
    """轮询基类测试"""

    @pytest.fixture
    def mock_poller(self):
        """创建可测试的轮询器"""
        from src.polling.factory import BasePolling

        class TestPoller(BasePolling):
            def _list_devices(self):
                return []

        return TestPoller

    def test_initialization(self, mock_poller):
        """测试初始化"""
        from src.polling.factory import BasePolling

        on_found = Mock()
        on_lost = Mock()

        poller = mock_poller(on_found, on_lost, interval=3.0)

        assert poller.interval == 3.0
        assert poller.on_device_found == on_found
        assert poller.on_device_lost == on_lost
        assert poller._running is False
        assert poller.known_devices == {}

    def test_start_and_stop(self, mock_poller):
        """测试启动和停止"""
        poller = mock_poller(Mock(), Mock(), interval=0.1)

        with patch.object(poller, '_list_devices', return_value=[]):
            poller.start()
            assert poller._running is True
            assert poller._thread is not None

            poller.stop()
            assert poller._running is False

    def test_stop_when_not_running(self, mock_poller):
        """测试停止未运行的轮询器"""
        poller = mock_poller(Mock(), Mock())
        # 不应该抛出异常
        poller.stop()

    def test_device_found_callback(self, mock_poller):
        """测试设备发现回调"""
        on_found = Mock()
        on_lost = Mock()

        poller = mock_poller(on_found, on_lost, interval=0.1)

        # 第一次返回空
        with patch.object(poller, '_list_devices', return_value=[]):
            # 手动触发检查
            poller._check_devices()

            # 没有新设备
            on_found.assert_not_called()

        # 第二次返回设备
        with patch.object(poller, '_list_devices', return_value=[{
            "device_id": "device-001",
            "platform": "android"
        }]):
            poller._check_devices()

            # 应该触发回调
            on_found.assert_called_once_with("device-001", {"device_id": "device-001", "platform": "android"})

    def test_device_lost_callback(self, mock_poller):
        """测试设备丢失回调"""
        on_found = Mock()
        on_lost = Mock()

        poller = mock_poller(on_found, on_lost, interval=0.1)

        # 预设已知设备
        poller._known_devices = {
            "device-001": {"device_id": "device-001", "platform": "android"}
        }

        with patch.object(poller, '_list_devices', return_value=[]):
            poller._check_devices()

            on_lost.assert_called_once_with("device-001")


class TestADBPolling:
    """ADB 轮询器测试"""

    def test_list_devices_returns_empty_on_error(self):
        """列出设备出错时返回空列表"""
        from src.polling.factory import ADBPolling

        poller = ADBPolling(Mock(), Mock())

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("ADB error")

            devices = poller._list_devices()

            assert devices == []

    def test_list_devices_parses_output(self):
        """解析 adb devices 输出"""
        from src.polling.factory import ADBPolling

        poller = ADBPolling(Mock(), Mock())

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                stdout="List of devices attached\ndevice-001 device product:MI 13 model:MI_13 device:usb:1A2B3C4D\ndevice-002 offline\n",
                returncode=0
            )

            devices = poller._list_devices()

            assert len(devices) == 1
            assert devices[0]["device_id"] == "device-001"
            assert devices[0]["platform"] == "android"
            assert devices[0]["model"] == "MI_13"
            assert devices[0]["connection"] == "usb"

    def test_list_devices_wifi_connection(self):
        """识别 WiFi 连接"""
        from src.polling.factory import ADBPolling

        poller = ADBPolling(Mock(), Mock())

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                stdout="List of devices attached\n192.168.1.100:5555 device\n",
                returncode=0
            )

            devices = poller._list_devices()

            assert len(devices) == 1
            assert devices[0]["connection"] == "wifi"


class TestHDCPolling:
    """HDC 轮询器测试"""

    def test_list_devices_returns_empty_on_error(self):
        """列出设备出错时返回空列表"""
        from src.polling.factory import HDCPolling

        poller = HDCPolling(Mock(), Mock())

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("HDC error")

            devices = poller._list_devices()

            assert devices == []

    def test_list_devices_parses_output(self):
        """解析 hdc list targets 输出"""
        from src.polling.factory import HDCPolling

        poller = HDCPolling(Mock(), Mock())

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                stdout="device-001\ndevice-002\n",
                returncode=0
            )

            devices = poller._list_devices()

            assert len(devices) == 2
            assert devices[0]["device_id"] == "device-001"
            assert devices[0]["platform"] == "harmonyos"
            assert devices[1]["device_id"] == "device-002"


class TestPollingFactory:
    """轮询工厂测试"""

    def test_create_adb_polling(self):
        """创建 ADB 轮询器"""
        from src.polling.factory import PollingFactory, PlatformType, ADBPolling

        poller = PollingFactory.create_polling(
            PlatformType.ADB,
            Mock(),
            Mock()
        )

        assert isinstance(poller, ADBPolling)

    def test_create_hdc_polling(self):
        """创建 HDC 轮询器"""
        from src.polling.factory import PollingFactory, PlatformType, HDCPolling

        poller = PollingFactory.create_polling(
            PlatformType.HDC,
            Mock(),
            Mock()
        )

        assert isinstance(poller, HDCPolling)

    def test_create_wda_polling(self):
        """创建 WDA 轮询器"""
        from src.polling.factory import PollingFactory, PlatformType, WDAPolling

        poller = PollingFactory.create_polling(
            PlatformType.WDA,
            Mock(),
            Mock()
        )

        assert isinstance(poller, WDAPolling)

    def test_unknown_platform_raises_error(self):
        """未知平台抛出异常"""
        from src.polling.factory import PollingFactory

        class UnknownPlatform:
            value = "unknown"

        with pytest.raises(ValueError, match="Unknown platform"):
            PollingFactory.create_polling(UnknownPlatform(), Mock(), Mock())


class TestPollingManager:
    """轮询管理器测试"""

    def test_initialization(self):
        """测试初始化"""
        from src.polling.manager import PollingManager

        manager = PollingManager()

        assert manager.interval == 3.0
        assert manager.enabled_platforms == set()
        assert manager.is_running is False
        assert manager.get_all_devices() == {}

    def test_enable_platform(self):
        """启用平台"""
        from src.polling.manager import PollingManager
        from src.polling.factory import PlatformType

        manager = PollingManager()

        manager.enable_platform(PlatformType.ADB)

        assert PlatformType.ADB in manager.enabled_platforms

    def test_enable_multiple_platforms(self):
        """启用多个平台"""
        from src.polling.manager import PollingManager
        from src.polling.factory import PlatformType

        manager = PollingManager()

        manager.enable_platform(PlatformType.ADB)
        manager.enable_platform(PlatformType.HDC)

        assert len(manager.enabled_platforms) == 2

    def test_disable_platform(self):
        """禁用平台"""
        from src.polling.manager import PollingManager
        from src.polling.factory import PlatformType

        manager = PollingManager()
        manager.enable_platform(PlatformType.ADB)

        manager.disable_platform(PlatformType.ADB)

        assert PlatformType.ADB not in manager.enabled_platforms

    def test_device_callbacks(self):
        """设备回调"""
        from src.polling.manager import PollingManager
        from src.polling.factory import PlatformType

        on_found = Mock()
        on_lost = Mock()

        manager = PollingManager(
            on_device_found=on_found,
            on_device_lost=on_lost
        )
        manager.enable_platform(PlatformType.ADB)

        # 模拟统一轮询器触发回调
        unified_poller = manager._unified_polling
        unified_poller.on_device_found("device-001", {"device_id": "device-001"})

        on_found.assert_called_once_with("device-001", {"device_id": "device-001"})

    def test_get_all_devices(self):
        """获取所有设备"""
        from src.polling.manager import PollingManager
        from src.polling.factory import PlatformType

        manager = PollingManager()
        manager.enable_platform(PlatformType.ADB)

        # 直接设置设备信息
        manager._device_info = {
            "device-001": {"device_id": "device-001", "platform": "android"},
            "device-002": {"device_id": "device-002", "platform": "harmonyos"},
        }

        devices = manager.get_all_devices()

        assert len(devices) == 2
        assert "device-001" in devices
        assert "device-002" in devices

    def test_get_devices_by_platform(self):
        """按平台获取设备"""
        from src.polling.manager import PollingManager

        manager = PollingManager()
        manager._device_info = {
            "device-001": {"device_id": "device-001", "platform": "android"},
            "device-002": {"device_id": "device-002", "platform": "harmonyos"},
            "device-003": {"device_id": "device-003", "platform": "android"},
        }

        android_devices = manager.get_devices_by_platform("android")

        assert len(android_devices) == 2

    def test_start_and_stop(self):
        """启动和停止"""
        from src.polling.manager import PollingManager
        from src.polling.factory import PlatformType

        manager = PollingManager()
        manager.enable_platform(PlatformType.ADB)

        with patch.object(manager._unified_polling, 'start'):
            manager.start()
            assert manager.is_running is True

        with patch.object(manager._unified_polling, 'stop'):
            manager.stop()
            assert manager.is_running is False


class TestUnifiedPolling:
    """统一轮询器测试"""

    def test_initialization(self):
        """测试初始化"""
        from src.polling.unified_polling import UnifiedPolling

        polling = UnifiedPolling(interval=5.0)

        assert polling.interval == 5.0
        assert polling._temp_devices == {}
        assert polling._previous_devices == {}
        assert polling._running is False

    def test_register_platform(self):
        """测试平台注册"""
        from src.polling.unified_polling import UnifiedPolling
        from src.polling.factory import PlatformType

        polling = UnifiedPolling()
        mock_lister = Mock(return_value=[])

        polling.register_platform(PlatformType.ADB, mock_lister)

        assert PlatformType.ADB in polling._platform_listers
        assert polling._platform_listers[PlatformType.ADB] == mock_lister

    def test_detect_new_device(self):
        """测试新增设备检测"""
        from src.polling.unified_polling import UnifiedPolling
        from src.polling.factory import PlatformType

        on_found = Mock()
        polling = UnifiedPolling(on_device_found=on_found)
        polling.register_platform(PlatformType.ADB, lambda: [{"device_id": "device-001", "platform": "android"}])

        polling._poll_once()

        on_found.assert_called_once_with("device-001", {"device_id": "device-001", "platform": "adb"})
        assert "device-001" in polling._previous_devices

    def test_detect_lost_device_after_threshold(self):
        """测试设备丢失检测（达到离线阈值）"""
        from src.polling.unified_polling import UnifiedPolling
        from src.polling.factory import PlatformType

        on_found = Mock()
        on_lost = Mock()
        polling = UnifiedPolling(
            on_device_found=on_found,
            on_device_lost=on_lost,
        )
        polling.register_platform(PlatformType.ADB, lambda: [{"device_id": "device-001", "platform": "android"}])

        # 第一次轮询：设备上线
        polling._poll_once()
        on_found.assert_called_once_with("device-001", {"device_id": "device-001", "platform": "adb"})
        on_lost.assert_not_called()

        # 连续3次轮询：设备丢失（每次返回空列表）
        polling.register_platform(PlatformType.ADB, lambda: [])
        for _ in range(UnifiedPolling.OFFLINE_THRESHOLD - 1):
            polling._poll_once()
            on_lost.assert_not_called()  # 还没达到阈值

        # 第3次：达到阈值，触发 lost
        polling._poll_once()
        assert on_lost.call_count == 1

    def test_polling_cycle_complete_callback(self):
        """测试轮询周期完成回调"""
        from src.polling.unified_polling import UnifiedPolling
        from src.polling.factory import PlatformType

        callback = Mock()
        polling = UnifiedPolling(
            interval=1.0,
            on_polling_cycle_complete=callback,
        )
        polling.register_platform(PlatformType.ADB, lambda: [{"device_id": "device-001", "platform": "android"}])

        polling._poll_once()

        callback.assert_called_once()
        temp, prev = callback.call_args[0]
        assert "device-001" in temp
        assert "device-001" in prev

    def test_multiple_platforms(self):
        """测试多平台轮询"""
        from src.polling.unified_polling import UnifiedPolling
        from src.polling.factory import PlatformType

        on_found = Mock()
        polling = UnifiedPolling(on_device_found=on_found)

        def adb_lister():
            return [{"device_id": "adb-001", "platform": "android"}]

        def hdc_lister():
            return [{"device_id": "hdc-001", "platform": "harmonyos"}]

        polling.register_platform(PlatformType.ADB, adb_lister)
        polling.register_platform(PlatformType.HDC, hdc_lister)

        polling._poll_once()

        assert on_found.call_count == 2
        assert "adb-001" in polling._temp_devices
        assert "hdc-001" in polling._temp_devices
