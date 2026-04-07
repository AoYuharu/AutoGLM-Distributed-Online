"""
轮询基类和工厂

参照 DESIGN.md 中的轮询设计
"""
from abc import ABC, abstractmethod
from enum import Enum
from typing import Callable, Optional
import threading
import time


class PlatformType(Enum):
    """平台类型"""
    ADB = "adb"
    HDC = "hdc"
    WDA = "wda"


class BasePolling(ABC):
    """
    轮询基类

    启动一个轮询线程，定期检查设备连接状态。
    支持设备离线检测（连续3次检测不到才认为离线）
    """

    OFFLINE_THRESHOLD = 3  # 连续3次检测不到才认为离线

    def __init__(
        self,
        on_device_found: Callable[[str, dict], None],
        on_device_lost: Callable[[str], None],
        interval: float = 3.0,
        on_polling_cycle_complete: Optional[Callable[[], None]] = None,
    ):
        """
        初始化轮询器

        Args:
            on_device_found: 设备发现回调 (device_id, device_info)
            on_device_lost: 设备丢失回调 (device_id)
            interval: 轮询间隔（秒）
            on_polling_cycle_complete: 轮询周期完成回调
        """
        self.on_device_found = on_device_found
        self.on_device_lost = on_device_lost
        self.interval = interval
        self.on_polling_cycle_complete = on_polling_cycle_complete
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._known_devices: dict[str, dict] = {}
        self._device_offline_count: dict[str, int] = {}  # 设备离线检测计数

    def start(self) -> None:
        """启动轮询"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止轮询"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _poll_loop(self) -> None:
        """轮询循环"""
        while self._running:
            try:
                self._check_devices()
                # 轮询周期完成，触发回调
                if self.on_polling_cycle_complete:
                    self.on_polling_cycle_complete()
            except Exception as e:
                print(f"Polling error: {e}")
            time.sleep(self.interval)

    @abstractmethod
    def _list_devices(self) -> list[dict]:
        """
        列出当前连接的设备

        Returns:
            设备信息列表，每个设备包含 device_id, platform 等
        """
        raise NotImplementedError

    def _check_devices(self) -> None:
        """检查设备变化（支持离线检测：连续3次检测不到才认为离线）"""
        current_devices: dict[str, dict] = {}

        for device in self._list_devices():
            device_id = device.get("device_id", "")
            current_devices[device_id] = device

            # 重置离线计数（设备仍然在线）
            if device_id in self._device_offline_count:
                self._device_offline_count[device_id] = 0

            # 新设备
            if device_id not in self._known_devices:
                self.on_device_found(device_id, device)

        # 消失的设备 - 使用连续离线计数
        for device_id in list(self._known_devices.keys()):
            if device_id not in current_devices:
                # 设备丢失计数 +1
                self._device_offline_count[device_id] = self._device_offline_count.get(device_id, 0) + 1

                if self._device_offline_count[device_id] >= self.OFFLINE_THRESHOLD:
                    # 真正离线，触发回调
                    self.on_device_lost(device_id)
                    del self._known_devices[device_id]
                    del self._device_offline_count[device_id]
            else:
                # 设备仍然在线，重置计数
                self._device_offline_count[device_id] = 0

        self._known_devices = current_devices

    @property
    def known_devices(self) -> dict[str, dict]:
        """获取已知的设备"""
        return self._known_devices.copy()


class ADBPolling(BasePolling):
    """ADB 设备轮询器"""

    def __init__(
        self,
        on_device_found: Callable[[str, dict], None],
        on_device_lost: Callable[[str], None],
        interval: float = 3.0,
        on_polling_cycle_complete: Optional[Callable[[], None]] = None,
        adb_path: str = "adb"
    ):
        super().__init__(on_device_found, on_device_lost, interval, on_polling_cycle_complete)
        self.adb_path = adb_path

    def _list_devices(self) -> list[dict]:
        """列出 ADB 设备"""
        import subprocess

        try:
            result = subprocess.run(
                [self.adb_path, "devices", "-l"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            devices = []
            for line in result.stdout.strip().split("\n")[1:]:
                if not line.strip():
                    continue

                parts = line.split()
                if len(parts) >= 2:
                    device_id = parts[0]
                    status = parts[1]

                    if status != "device":
                        continue

                    # 解析设备信息
                    model = None
                    for part in parts[2:]:
                        if part.startswith("model:"):
                            model = part.split(":", 1)[1]
                            break

                    # 判断连接类型
                    connection = "usb"
                    if ":" in device_id:
                        connection = "wifi"

                    devices.append({
                        "device_id": device_id,
                        "platform": "android",
                        "status": status,
                        "model": model,
                        "connection": connection,
                    })

            return devices

        except Exception as e:
            print(f"Error listing ADB devices: {e}")
            return []


class HDCPolling(BasePolling):
    """HDC 设备轮询器"""

    def __init__(
        self,
        on_device_found: Callable[[str, dict], None],
        on_device_lost: Callable[[str], None],
        interval: float = 3.0,
        on_polling_cycle_complete: Optional[Callable[[], None]] = None,
        hdc_path: str = "hdc"
    ):
        super().__init__(on_device_found, on_device_lost, interval, on_polling_cycle_complete)
        self.hdc_path = hdc_path

    def _list_devices(self) -> list[dict]:
        """列出 HDC 设备"""
        import subprocess

        try:
            result = subprocess.run(
                [self.hdc_path, "list", "targets"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            devices = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue

                device_id = line.strip()

                # 判断连接类型
                connection = "usb"
                if ":" in device_id:
                    connection = "wifi"

                devices.append({
                    "device_id": device_id,
                    "platform": "harmonyos",
                    "status": "device",
                    "connection": connection,
                })

            return devices

        except Exception as e:
            print(f"Error listing HDC devices: {e}")
            return []


class WDAPolling(BasePolling):
    """WDA 设备轮询器"""

    def __init__(
        self,
        on_device_found: Callable[[str, dict], None],
        on_device_lost: Callable[[str], None],
        interval: float = 3.0,
        on_polling_cycle_complete: Optional[Callable[[], None]] = None,
        wda_url: str = "http://localhost:8100"
    ):
        super().__init__(on_device_found, on_device_lost, interval, on_polling_cycle_complete)
        self.wda_url = wda_url

    def _list_devices(self) -> list[dict]:
        """列出 WDA 设备"""
        import requests

        devices = []
        try:
            # 检查 WDA 服务状态
            response = requests.get(
                f"{self.wda_url}/status",
                timeout=5
            )

            if response.status_code == 200:
                data = response.json()
                session = data.get("value", {}).get("session", {})

                if session:
                    # 有活跃会话，说明设备连接
                    device_info = session.get("device", {})
                    udid = device_info.get("udid") or "unknown"

                    devices.append({
                        "device_id": f"{self.wda_url}:{udid}",
                        "platform": "ios",
                        "status": "device",
                        "udid": udid,
                        "wda_url": self.wda_url,
                        "os_version": device_info.get("osVersion", ""),
                        "model": device_info.get("name", "iPhone"),
                        "connection": "usb",  # WDA 通常通过 USB
                    })

        except requests.exceptions.RequestException as e:
            print(f"WDA polling error: {e}")
        except Exception as e:
            print(f"Error listing WDA devices: {e}")

        return devices


class PollingFactory:
    """轮询器工厂"""

    @staticmethod
    def create_polling(
        platform: PlatformType,
        on_device_found: Callable[[str, dict], None],
        on_device_lost: Callable[[str], None],
        interval: float = 3.0,
        on_polling_cycle_complete: Optional[Callable[[], None]] = None,
        **kwargs
    ) -> BasePolling:
        """
        创建轮询器

        Args:
            platform: 平台类型
            on_device_found: 设备发现回调
            on_device_lost: 设备丢失回调
            interval: 轮询间隔
            on_polling_cycle_complete: 轮询周期完成回调
            **kwargs: 平台特定参数

        Returns:
            对应平台的轮询器
        """
        if platform == PlatformType.ADB:
            return ADBPolling(
                on_device_found,
                on_device_lost,
                interval,
                on_polling_cycle_complete=on_polling_cycle_complete,
                adb_path=kwargs.get("adb_path", "adb")
            )
        elif platform == PlatformType.HDC:
            return HDCPolling(
                on_device_found,
                on_device_lost,
                interval,
                on_polling_cycle_complete=on_polling_cycle_complete,
                hdc_path=kwargs.get("hdc_path", "hdc")
            )
        elif platform == PlatformType.WDA:
            return WDAPolling(
                on_device_found,
                on_device_lost,
                interval,
                on_polling_cycle_complete=on_polling_cycle_complete,
                wda_url=kwargs.get("wda_url", "http://localhost:8100")
            )
        else:
            raise ValueError(f"Unknown platform: {platform}")
