"""
统一轮询器

在单一线程中依次轮询所有平台，汇总后统一处理
"""
import logging
import threading
import time
from typing import Callable, Optional, List, Dict

from src.polling.factory import PlatformType

# 模块日志器
_logger = logging.getLogger(__name__)


class UnifiedPolling:
    """
    统一轮询器

    在单一线程中依次轮询所有平台，汇总后统一处理:
    1. 搜索所有平台 → temp 表
    2. 对比 temp vs previous → 检测新增和消失的设备
    3. 更新 previous 表
    4. 触发回调
    """

    OFFLINE_THRESHOLD = 3  # 连续3次检测不到才认为离线

    def __init__(
        self,
        interval: float = 3.0,
        on_device_found: Optional[Callable[[str, dict], None]] = None,
        on_device_lost: Optional[Callable[[str], None]] = None,
        on_polling_cycle_complete: Optional[Callable[[dict, dict], None]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            interval: 轮询间隔（秒）
            on_device_found: 设备发现回调 (device_id, device_info)
            on_device_lost: 设备丢失回调 (device_id)
            on_polling_cycle_complete: 轮询周期完成回调 (temp_devices, previous_devices)
            logger: 日志记录器
        """
        self.interval = interval
        self.on_device_found = on_device_found
        self.on_device_lost = on_device_lost
        self.on_polling_cycle_complete = on_polling_cycle_complete
        self._logger = logger or _logger

        # temp 表：当前轮询周期检测到的设备
        self._temp_devices: Dict[str, dict] = {}
        # previous 表：上一轮检测到的设备
        self._previous_devices: Dict[str, dict] = {}

        # 设备离线计数（连续检测不到次数）
        self._device_offline_count: dict[str, int] = {}

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 平台轮询函数注册
        self._platform_listers: Dict[PlatformType, Callable[[], List[dict]]] = {}

    def register_platform(self, platform: PlatformType, lister: Callable[[], List[dict]]) -> None:
        """注册平台设备列表函数"""
        self._platform_listers[platform] = lister
        self._logger.info(f"[register_platform] Registered platform: {platform.value}")

    def start(self) -> None:
        """启动轮询"""
        if self._running:
            self._logger.debug("[start] UnifiedPolling already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._logger.info("[start] UnifiedPolling started")

    def stop(self) -> None:
        """停止轮询"""
        self._logger.info("[stop] Stopping UnifiedPolling")
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._logger.info("[stop] UnifiedPolling stopped")

    def _poll_loop(self) -> None:
        """轮询循环"""
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                self._logger.error(f"Polling error: {e}")
            time.sleep(self.interval)

    def _poll_once(self) -> None:
        """执行一次统一轮询"""
        # 步骤1: 清空 temp 表，搜索所有平台
        self._temp_devices.clear()

        for platform, lister in self._platform_listers.items():
            try:
                devices = lister()
                for dev in devices:
                    device_id = dev.get("device_id", "")
                    if device_id:
                        dev["platform"] = platform.value
                        self._temp_devices[device_id] = dev
                        self._logger.debug(f"[poll_once] Found device {device_id} on {platform.value}")
            except Exception as e:
                self._logger.error(f"[poll_once] Error listing {platform.value} devices: {e}")

        # 步骤2: 对比 previous vs temp，检测新增和消失的设备（传入引用避免被覆盖）
        self._detect_changes(self._previous_devices, self._temp_devices)

        # 步骤3: 更新 previous 表（保留当前存在的设备）
        # 注意：丢失的设备在 _detect_changes 中通过离线计数追踪，不立即从 previous 中移除
        for device_id in list(self._previous_devices.keys()):
            if device_id not in self._temp_devices and self._device_offline_count.get(device_id, 0) >= self.OFFLINE_THRESHOLD:
                # 设备真正离线后，从 previous 中移除
                del self._previous_devices[device_id]
        # 添加或更新当前存在的设备
        for device_id, dev in self._temp_devices.items():
            self._previous_devices[device_id] = dev.copy()

        # 步骤4: 触发轮询周期完成回调
        if self.on_polling_cycle_complete:
            try:
                self.on_polling_cycle_complete(self._temp_devices, self._previous_devices)
            except Exception as e:
                self._logger.error(f"[poll_once] Error in on_polling_cycle_complete: {e}")

    def _detect_changes(self, previous_devices: Dict[str, dict], current_devices: Dict[str, dict]) -> None:
        """检测设备变化

        Args:
            previous_devices: 上一轮检测到的设备
            current_devices: 当前检测到的设备
        """
        # 新增的设备
        for device_id, device_info in current_devices.items():
            if device_id not in previous_devices:
                # 重置离线计数
                self._device_offline_count[device_id] = 0
                self._logger.info(f"[detect_changes] Device found: {device_id}")
                if self.on_device_found:
                    try:
                        self.on_device_found(device_id, device_info)
                    except Exception as e:
                        self._logger.error(f"[detect_changes] Error in on_device_found for {device_id}: {e}")

        # 消失的设备 - 使用离线计数
        for device_id in list(previous_devices.keys()):
            if device_id not in current_devices:
                # 设备丢失计数 +1
                self._device_offline_count[device_id] = self._device_offline_count.get(device_id, 0) + 1

                if self._device_offline_count[device_id] >= self.OFFLINE_THRESHOLD:
                    # 真正离线，触发回调
                    self._logger.info(f"[detect_changes] Device lost: {device_id} (offline after {self._device_offline_count[device_id]} checks)")
                    if self.on_device_lost:
                        try:
                            self.on_device_lost(device_id)
                        except Exception as e:
                            self._logger.error(f"[detect_changes] Error in on_device_lost for {device_id}: {e}")
                    # 清理离线计数
                    del self._device_offline_count[device_id]
            else:
                # 设备仍然在线，重置计数
                self._device_offline_count[device_id] = 0

    def get_temp_devices(self) -> Dict[str, dict]:
        """获取当前轮询周期的设备"""
        with self._lock:
            return self._temp_devices.copy()

    def get_previous_devices(self) -> Dict[str, dict]:
        """获取上一轮轮询周期的设备"""
        with self._lock:
            return self._previous_devices.copy()

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running
