"""
Simplified WebSocket Message Protocol for Server <-> Client Communication

Primary runtime paths:
- Client -> Server: device_status (HTTP), observe_result (HTTP)
- Server -> Client: action_cmd (WS)
- Bidirectional: ack (WS)
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from typing_extensions import Literal

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    DEVICE_REGISTER = "device_register"
    DEVICE_STATUS = "device_status"
    OBSERVE_RESULT = "observe_result"
    ACTION_CMD = "action_cmd"
    REQUEST_SCREENSHOT = "request_screenshot"
    ACK = "ack"


class WSMessage(BaseModel):
    msg_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: MessageType
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    version: str = "1.0"
    payload: dict = Field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "type": self.type.value,
            "timestamp": self.timestamp,
            "version": self.version,
            "payload": self.payload,
        }


class DeviceRegisterPayload(BaseModel):
    device_id: str
    platform: str
    capabilities: dict = Field(default_factory=dict)
    client_id: Optional[str] = None


class DeviceStatusPayload(BaseModel):
    device_id: str
    status: str
    delta: dict = Field(default_factory=dict)


class ObserveResultPayload(BaseModel):
    task_id: str
    device_id: str
    step_number: int
    screenshot: Optional[str] = None
    result: str
    success: bool
    error: Optional[str] = None
    version: Optional[int] = None


class ActionPayload(BaseModel):
    action_type: str
    element: Optional[list[int]] = None
    start: Optional[list[int]] = None
    end: Optional[list[int]] = None
    text: Optional[str] = None
    app: Optional[str] = None
    duration: Optional[int] = None
    message: Optional[str] = None

    def to_dict(self) -> dict:
        result = {"action": self.action_type}
        if self.element:
            result["element"] = self.element
        if self.start:
            result["start"] = self.start
        if self.end:
            result["end"] = self.end
        if self.text:
            result["text"] = self.text
        if self.app:
            result["app"] = self.app
        if self.duration is not None:
            result["duration"] = self.duration
        if self.message:
            result["message"] = self.message
        return result


class ActionCmdPayload(BaseModel):
    task_id: str
    device_id: str
    step_number: int
    action: dict = Field(default_factory=dict)
    reasoning: str = ""


class RequestScreenshotPayload(BaseModel):
    type: Literal["request_screenshot"] = "request_screenshot"
    task_id: str
    device_id: str
    step_number: int = 0
    phase: str = "observe"
    purpose: str = "bootstrap"


def create_message(msg_type: MessageType, payload: dict, version: str = "1.0") -> WSMessage:
    return WSMessage(type=msg_type, payload=payload, version=version)


def create_device_register(
    device_id: str,
    platform: str,
    capabilities: dict = None,
    client_id: str = None,
) -> WSMessage:
    return create_message(
        MessageType.DEVICE_REGISTER,
        {
            "device_id": device_id,
            "platform": platform,
            "capabilities": capabilities or {},
            "client_id": client_id,
        },
    )


def create_device_status(device_id: str, status: str, delta: dict = None) -> WSMessage:
    return create_message(
        MessageType.DEVICE_STATUS,
        {
            "device_id": device_id,
            "status": status,
            "delta": delta or {},
        },
    )


def create_observe_result(
    task_id: str,
    device_id: str,
    step_number: int,
    result: str,
    success: bool,
    screenshot: str = None,
    error: str = None,
    version: Optional[int] = None,
) -> WSMessage:
    payload = {
        "task_id": task_id,
        "device_id": device_id,
        "step_number": step_number,
        "result": result,
        "success": success,
        "screenshot": screenshot,
        "error": error,
    }
    if version is not None:
        payload["version"] = version
    return create_message(MessageType.OBSERVE_RESULT, payload)


def create_action_cmd(
    task_id: str,
    device_id: str,
    step_number: int,
    action: dict,
    reasoning: str = "",
    version: str = "1.0",
) -> WSMessage:
    return create_message(
        MessageType.ACTION_CMD,
        {
            "task_id": task_id,
            "device_id": device_id,
            "step_number": step_number,
            "action": action,
            "reasoning": reasoning,
        },
        version=version,
    )


def create_request_screenshot(
    task_id: str,
    device_id: str,
    *,
    step_number: int = 0,
    phase: str = "observe",
    purpose: str = "bootstrap",
) -> WSMessage:
    payload = RequestScreenshotPayload(
        task_id=task_id,
        device_id=device_id,
        step_number=step_number,
        phase=phase,
        purpose=purpose,
    )
    return create_message(MessageType.REQUEST_SCREENSHOT, payload.model_dump())


def create_task_update(
    task_id: str,
    device_id: str,
    status: str,
    step: int = 0,
    progress: dict = None,
) -> WSMessage:
    from enum import Enum as _Enum

    class TaskUpdateType(str, _Enum):
        TASK_UPDATE = "task_update"

    return WSMessage(
        type=TaskUpdateType.TASK_UPDATE,
        payload={
            "task_id": task_id,
            "device_id": device_id,
            "status": status,
            "step": step,
            "progress": progress or {},
        },
    )


def create_agent_event(
    device_id: str,
    task_id: str,
    event_type: str,
    data: dict = None,
) -> WSMessage:
    from enum import Enum as _Enum

    class AgentEventType(str, _Enum):
        AGENT_EVENT = "agent_event"

    return WSMessage(
        type=AgentEventType.AGENT_EVENT,
        payload={
            "device_id": device_id,
            "task_id": task_id,
            "event_type": event_type,
            "data": data or {},
        },
    )
