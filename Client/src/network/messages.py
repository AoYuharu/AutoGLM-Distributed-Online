"""
消息定义与序列化 - 精简版

只保留:
- HTTP: device_status, observe_result
- WebSocket: ack (Client→Server), action_cmd (Server→Client)
"""
from dataclasses import dataclass, asdict, field
from typing import Optional, Any
from datetime import datetime
from enum import Enum
import json
import uuid


# ==================== 错误码定义 ====================

class AckErrorCode(Enum):
    """ACK 错误码"""
    VERSION_MISMATCH = 1   # 版本不匹配
    DEVICE_OFFLINE = 2     # 目标设备 offline 或不存在


# ==================== 消息类型定义 ====================

class MessageType(Enum):
    """消息类型 - 精简版"""
    ACK = "ack"
    ACTION_CMD = "action_cmd"


@dataclass
class BaseMessage:
    """基础消息类"""
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: str = "1.0"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat() + "Z")

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BaseMessage":
        """从字典创建"""
        data = data.copy()
        data.pop("timestamp", None)
        return cls(**data)

    def to_json(self) -> str:
        """转换为 JSON"""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "BaseMessage":
        """从 JSON 创建"""
        return cls.from_dict(json.loads(json_str))


# ==================== WebSocket 消息 ====================

@dataclass
class ActionCmdMessage(BaseMessage):
    """动作命令 (Server → Client)"""
    type: str = "action_cmd"
    payload: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "ActionCmdMessage":
        """从字典创建 ActionCmdMessage"""
        msg_id = data.get("msg_id", str(uuid.uuid4()))
        version = data.get("version", "1.0")
        payload = data.get("payload", {})
        timestamp = data.get("timestamp", datetime.now().isoformat() + "Z")
        return cls(
            msg_id=msg_id,
            version=version,
            timestamp=timestamp,
            payload=payload,
        )

    @property
    def task_id(self) -> str:
        return self.payload.get("task_id", "")

    @property
    def device_id(self) -> str:
        return self.payload.get("device_id", "")

    @property
    def step_number(self) -> int:
        return self.payload.get("step_number", 0)

    @property
    def action(self) -> dict:
        return self.payload.get("action", {})

    @property
    def reasoning(self) -> str:
        return self.payload.get("reasoning", "")


@dataclass
class AckMessage(BaseMessage):
    """消息确认 (Client → Server)"""
    type: str = "ack"
    ref_msg_id: str = ""
    payload: dict = field(default_factory=lambda: {
        "accepted": True,
        "device_id": "",
        "error": None,
        "error_code": None,
    })

    @classmethod
    def create(
        cls,
        ref_msg_id: str,
        accepted: bool = True,
        device_id: str = "",
        error: Optional[str] = None,
        error_code: Optional[int] = None,
    ) -> "AckMessage":
        """创建 ACK 消息"""
        return cls(
            msg_id=str(uuid.uuid4()),
            ref_msg_id=ref_msg_id,
            payload={
                "accepted": accepted,
                "device_id": device_id,
                "error": error,
                "error_code": error_code,
            }
        )


# ==================== HTTP 消息 ====================

@dataclass
class DeviceStatusMessage(BaseMessage):
    """设备状态上报 (Client → Server) - HTTP POST 格式"""
    type: str = "device_status"
    client_id: str = ""
    payload: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        client_id: str,
        devices: list[dict]
    ) -> "DeviceStatusMessage":
        """创建设备状态消息"""
        return cls(
            msg_id=str(uuid.uuid4()),
            client_id=client_id,
            payload={"devices": devices}
        )

    def to_dict(self) -> dict:
        """转换为字典（兼容 HTTP 格式）"""
        result = {
            "msg_id": self.msg_id,
            "type": self.type,
            "version": self.version,
            "timestamp": self.timestamp,
            "client_id": self.client_id,
            "payload": self.payload,
        }
        return result


@dataclass
class ObserveResultMessage(BaseMessage):
    """观察结果上报 (Client → Server) - HTTP POST 格式"""
    type: str = "observe_result"
    payload: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        task_id: str,
        device_id: str,
        step_number: int,
        screenshot: Optional[str] = None,
        result: str = "",
        success: bool = True,
        error: Optional[str] = None,
    ) -> "ObserveResultMessage":
        """创建观察结果消息"""
        return cls(
            msg_id=str(uuid.uuid4()),
            payload={
                "task_id": task_id,
                "device_id": device_id,
                "step_number": step_number,
                "screenshot": screenshot,
                "result": result,
                "success": success,
                "error": error,
            }
        )


# ==================== 消息工厂 ====================

class MessageFactory:
    """消息工厂"""

    @staticmethod
    def from_dict(data: dict) -> BaseMessage:
        """
        从字典创建消息对象

        Args:
            data: 消息字典

        Returns:
            对应的消息对象
        """
        msg_type = data.get("type")

        if msg_type == "action_cmd":
            return ActionCmdMessage.from_dict(data)
        elif msg_type == "ack":
            return AckMessage.from_dict(data)
        else:
            raise ValueError(f"Unknown message type: {msg_type}")
