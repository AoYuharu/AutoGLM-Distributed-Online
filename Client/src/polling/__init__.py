"""
Polling module - 设备轮询模块

参照 DESIGN.md 中的轮询设计
"""
from src.polling.factory import PlatformType, PollingFactory
from src.polling.manager import PollingManager

__all__ = [
    "PlatformType",
    "PollingFactory",
    "PollingManager",
]
