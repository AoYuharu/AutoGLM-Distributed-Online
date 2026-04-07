"""
Device adapter base classes and interfaces.
参照 phone_agent/adb/connection.py 和 phone_agent/adb/device.py 实现
"""
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any
from enum import Enum

# 模块日志器
_logger = logging.getLogger(__name__)


class Platform(Enum):
    """支持的平台类型"""
    ANDROID = "android"
    HARMONYOS = "harmonyos"
    IOS = "ios"


@dataclass
class ActionResult:
    """动作执行结果"""
    success: bool
    should_finish: bool
    message: str | None = None
    requires_confirmation: bool = False


@dataclass
class DeviceCapabilities:
    """设备能力"""
    platform: Platform
    screenshot: bool = True
    input_text: bool = True
    system_buttons: list[str] = field(default_factory=list)  # back, home, power
    battery: bool = False
    screen_size: tuple[int, int] = (1080, 2400)
    os_version: str = ""
    supported_apps: list[str] = field(default_factory=list)
    api_level: Optional[int] = None
    device_name: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "platform": self.platform.value if isinstance(self.platform, Platform) else self.platform,
            "screenshot": self.screenshot,
            "input_text": self.input_text,
            "system_buttons": self.system_buttons,
            "battery": self.battery,
            "screen_size": list(self.screen_size),
            "os_version": self.os_version,
            "supported_apps": self.supported_apps,
            "api_level": self.api_level,
            "device_name": self.device_name,
            "extra": self.extra,
        }


class DeviceAdapterBase(ABC):
    """
    设备适配器基类

    所有平台适配器必须继承此类并实现抽象方法。
    """

    def __init__(self, device_id: str, logger: Optional[logging.Logger] = None):
        self.device_id = device_id
        self._capabilities: Optional[DeviceCapabilities] = None
        self._platform: Platform = Platform.ANDROID  # 子类应覆盖
        self._logger = logger or _logger

    @abstractmethod
    async def check_capabilities(self) -> DeviceCapabilities:
        """
        首次连接时检查，返回设备能力

        Returns:
            DeviceCapabilities: 设备能力对象
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """
        心跳检测

        Returns:
            bool: 设备是否在线
        """
        raise NotImplementedError

    @abstractmethod
    def get_screenshot(self) -> bytes:
        """
        获取截图

        Returns:
            bytes: PNG 格式的截图数据
        """
        raise NotImplementedError

    @abstractmethod
    def execute_action(self, action: dict) -> ActionResult:
        """
        执行动作

        Args:
            action: 动作字典，包含 action 类型和参数

        Returns:
            ActionResult: 执行结果
        """
        raise NotImplementedError

    @property
    def capabilities(self) -> Optional[DeviceCapabilities]:
        """获取已检查的能力，未检查时返回 None"""
        return self._capabilities

    @property
    def is_available(self) -> bool:
        """设备是否可用（能力已检查）"""
        return self._capabilities is not None

    @property
    def platform(self) -> Platform:
        """获取平台类型"""
        return self._platform

    def _resolve_action_type(self, action: dict) -> str:
        """Resolve action type from either legacy or server payloads."""
        metadata = action.get("_metadata")
        if isinstance(metadata, str) and metadata:
            return metadata.lower()

        action_name = str(action.get("action", "")).strip().lower()
        if action_name in {"finish", "stop", "done"}:
            return "finish"
        if action_name:
            return "do"
        return "unknown"

    def _normalize_action_name(self, action: dict) -> str:
        """Normalize action name for adapter dispatch."""
        return str(action.get("action", "unknown")).strip().lower()

    def _convert_relative_to_absolute(
        self, element: dict, screen_width: int, screen_height: int
    ) -> tuple[int, int]:
        """
        将相对坐标 (0-999) 转换为绝对像素坐标

        Args:
            element: 包含 x, y 的字典
            screen_width: 屏幕宽度
            screen_height: 屏幕高度

        Returns:
            tuple: (abs_x, abs_y)
        """
        rel_x = element.get("x", 500)
        rel_y = element.get("y", 500)
        abs_x = int(rel_x / 1000 * screen_width)
        abs_y = int(rel_y / 1000 * screen_height)
        return abs_x, abs_y

    # === 通用动作方法（可被子类覆盖） ===

    def tap(self, x: int, y: int) -> None:
        """点击"""
        raise NotImplementedError

    def double_tap(self, x: int, y: int) -> None:
        """双击"""
        raise NotImplementedError

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> None:
        """长按"""
        raise NotImplementedError

    def swipe(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int = 300
    ) -> None:
        """滑动"""
        raise NotImplementedError

    def back(self) -> None:
        """返回键"""
        raise NotImplementedError

    def home(self) -> None:
        """Home 键"""
        raise NotImplementedError

    def launch_app(self, package: str) -> bool:
        """启动应用"""
        raise NotImplementedError

    def type_text(self, text: str) -> None:
        """输入文本"""
        raise NotImplementedError

    def _log(self, level: str, message: str, **kwargs) -> None:
        """内部日志方法"""
        if self._logger:
            getattr(self._logger, level.lower())(message, **kwargs)
