"""
轮询管理器

管理多个平台的轮询器，统一处理设备事件
"""
import logging
from typing import Callable, Optional
import threading

from src.polling.unified_polling import UnifiedPolling
from src.polling.factory import (
    PlatformType,
    ADBPolling,
    HDCPolling,
    WDAPolling,
)

# 模块日志器
_logger = logging.getLogger(__name__)


class PollingManager:
    """
    轮询管理器

    使用 UnifiedPolling 统一轮询所有平台:
    1. 搜索所有平台 → temp 表
    2. 对比 temp vs previous → 检测新增和消失的设备
    3. capabilities check（在 DistributedClient 中进行）
    4. 全量上报（在 DistributedClient 中进行）
    """

    def __init__(
        self,
        on_device_found: Optional[Callable[[str, dict], None]] = None,
        on_device_lost: Optional[Callable[[str], None]] = None,
        on_polling_cycle_complete: Optional[Callable[[dict, dict], None]] = None,
        interval: float = 3.0,
        adb_binary: str = "adb",
        hdc_binary: str = "hdc",
        wda_url: str = "http://localhost:8100",
        logger: Optional[logging.Logger] = None,
    ):
        """
        初始化轮询管理器

        Args:
            on_device_found: 设备发现回调 (device_id, device_info)
            on_device_lost: 设备丢失回调 (device_id)
            on_polling_cycle_complete: 轮询周期完成回调 (temp_devices, previous_devices)
            interval: 轮询间隔（秒）
            logger: 可选的日志记录器
        """
        self.on_device_found = on_device_found
        self.on_device_lost = on_device_lost
        self.on_polling_cycle_complete = on_polling_cycle_complete
        self.interval = interval
        self.adb_binary = adb_binary
        self.hdc_binary = hdc_binary
        self.wda_url = wda_url
        self._logger = logger or _logger

        # 统一轮询器
        self._unified_polling: Optional[UnifiedPolling] = None

        # 已启用的平台配置
        self._enabled_platforms: set[PlatformType] = set()

        # 设备信息缓存（由 on_device_found/on_device_lost 更新）
        self._device_info: dict[str, dict] = {}
        self._lock = threading.Lock()

        self._running = False

        self._logger.info("[PollingManager] Initialized")

    def enable_platform(self, platform: PlatformType, **kwargs) -> None:
        """
        启用平台轮询

        Args:
            platform: 平台类型
            **kwargs: 平台特定参数
        """
        if platform in self._enabled_platforms:
            self._logger.debug(f"[enable_platform] Platform {platform.value} already enabled")
            return

        self._logger.info(f"[enable_platform] Enabling platform: {platform.value}")
        self._enabled_platforms.add(platform)

        # 懒创建统一轮询器
        if self._unified_polling is None:
            self._unified_polling = UnifiedPolling(
                interval=self.interval,
                on_device_found=self._wrap_on_device_found,
                on_device_lost=self._wrap_on_device_lost,
                on_polling_cycle_complete=self.on_polling_cycle_complete,
                logger=self._logger,
            )

        # 注册平台的 lister
        self._register_platform_lister(platform, **kwargs)

        self._logger.info(f"[enable_platform] Platform {platform.value} enabled")

    def _register_platform_lister(self, platform: PlatformType, **kwargs) -> None:
        """注册平台的设备列表函数"""
        if platform == PlatformType.ADB:
            adb_path = kwargs.get("adb_path", self.adb_binary)
            # 临时创建 poller 来获取 _list_devices 方法
            temp_poller = ADBPolling(
                on_device_found=lambda *args: None,
                on_device_lost=lambda *args: None,
                interval=self.interval,
                adb_path=adb_path,
            )
            self._unified_polling.register_platform(platform, temp_poller._list_devices)

        elif platform == PlatformType.HDC:
            hdc_path = kwargs.get("hdc_path", self.hdc_binary)
            temp_poller = HDCPolling(
                on_device_found=lambda *args: None,
                on_device_lost=lambda *args: None,
                interval=self.interval,
                hdc_path=hdc_path,
            )
            self._unified_polling.register_platform(platform, temp_poller._list_devices)

        elif platform == PlatformType.WDA:
            wda_url = kwargs.get("wda_url", self.wda_url)
            temp_poller = WDAPolling(
                on_device_found=lambda *args: None,
                on_device_lost=lambda *args: None,
                interval=self.interval,
                wda_url=wda_url,
            )
            self._unified_polling.register_platform(platform, temp_poller._list_devices)

    def disable_platform(self, platform: PlatformType) -> None:
        """
        禁用平台轮询

        Args:
            platform: 平台类型
        """
        if platform not in self._enabled_platforms:
            self._logger.debug(f"[disable_platform] Platform {platform.value} not enabled")
            return

        self._logger.info(f"[disable_platform] Disabling platform: {platform.value}")
        self._enabled_platforms.discard(platform)

        # TODO: 如果需要动态取消注册平台，需要在 UnifiedPolling 中添加对应方法
        # 目前通过重启实现
        self._logger.info(f"[disable_platform] Platform {platform.value} disabled")

    def _wrap_on_device_found(self, device_id: str, device_info: dict) -> None:
        """包装设备发现回调"""
        with self._lock:
            self._device_info[device_id] = device_info
        self._logger.info(f"[_wrap_on_device_found] Device found: {device_id}")
        if self.on_device_found:
            self.on_device_found(device_id, device_info)

    def _wrap_on_device_lost(self, device_id: str) -> None:
        """包装设备丢失回调"""
        with self._lock:
            self._device_info.pop(device_id, None)
        self._logger.info(f"[_wrap_on_device_lost] Device lost: {device_id}")
        if self.on_device_lost:
            self.on_device_lost(device_id)

    def start(self) -> None:
        """启动轮询"""
        if self._running:
            self._logger.debug("[start] PollingManager already running")
            return

        if not self._unified_polling:
            self._logger.warning("[start] No platforms enabled, skipping start")
            return

        self._logger.info("[start] Starting PollingManager")
        self._running = True
        self._unified_polling.start()
        self._logger.info("[start] PollingManager started")

    def stop(self) -> None:
        """停止轮询"""
        self._logger.info("[stop] Stopping PollingManager")
        self._running = False

        if self._unified_polling:
            self._unified_polling.stop()
        self._logger.info("[stop] PollingManager stopped")

    def get_all_devices(self) -> dict[str, dict]:
        """
        获取所有已知设备

        Returns:
            设备信息字典 {device_id: device_info}
        """
        with self._lock:
            return self._device_info.copy()

    def get_devices_by_platform(self, platform: str) -> list[dict]:
        """
        获取指定平台的设备

        Args:
            platform: 平台名称

        Returns:
            设备列表
        """
        with self._lock:
            return [
                info for info in self._device_info.values()
                if info.get("platform") == platform
            ]

    def get_device(self, device_id: str) -> Optional[dict]:
        """
        获取指定设备信息

        Args:
            device_id: 设备 ID

        Returns:
            设备信息，如果不存在返回 None
        """
        with self._lock:
            return self._device_info.get(device_id)

    @property
    def enabled_platforms(self) -> set[PlatformType]:
        """获取已启用的平台"""
        return self._enabled_platforms.copy()

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running
