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

from src.config import settings


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
    session_id: Optional[str] = None
    run_id: Optional[str] = None

    @property
    def effective_session_id(self) -> str:
        return self.session_id or self.task_id


class SessionScopedPayload(BaseModel):
    task_id: str
    session_id: Optional[str] = None
    run_id: Optional[str] = None

    @property
    def effective_session_id(self) -> str:
        return self.session_id or self.task_id


class ActionCmdPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    action: dict = Field(default_factory=dict)
    reasoning: str = ""


class RequestScreenshotPayload(SessionScopedPayload):
    type: Literal["request_screenshot"] = "request_screenshot"
    device_id: str
    step_number: int = 0
    phase: str = "observe"
    purpose: str = "bootstrap"


class AgentEventPayload(SessionScopedPayload):
    device_id: str
    event_type: str
    data: dict = Field(default_factory=dict)


class TaskUpdatePayload(SessionScopedPayload):
    device_id: str
    status: str
    step: int = 0
    progress: dict = Field(default_factory=dict)


class DeviceBusyPayload(SessionScopedPayload):
    device_id: str
    current_session_id: Optional[str] = None
    current_run_id: Optional[str] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None

    @property
    def effective_current_session_id(self) -> Optional[str]:
        return self.current_session_id or self.session_id or self.task_id


class InterruptPayload(SessionScopedPayload):
    device_id: str


class ConfirmPhasePayload(SessionScopedPayload):
    device_id: str
    approved: bool


class ObserveErrorDecisionPayload(SessionScopedPayload):
    device_id: str
    decision: str
    advice: Optional[str] = None


class TaskCreatedPayload(SessionScopedPayload):
    device_id: str
    status: str = "pending"
    instruction: Optional[str] = None
    mode: Optional[str] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    session_run_count: Optional[int] = None

    @property
    def effective_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class AgentStatusPayload(SessionScopedPayload):
    device_id: str
    status: str
    message: str
    data: dict = Field(default_factory=dict)


class AgentProgressPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    phase: str
    stage: str
    message: str
    version: Optional[int] = None
    data: dict = Field(default_factory=dict)


class AgentStepPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    reasoning: str = ""
    action: dict = Field(default_factory=dict)
    result: str = ""
    screenshot: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    error_type: Optional[str] = None


class DeviceSyncEntry(BaseModel):
    device_id: str
    status: str
    last_update: str
    current_task_id: Optional[str] = None
    current_session_id: Optional[str] = None
    current_run_id: Optional[str] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None


class DeviceSyncPayload(BaseModel):
    devices: list[DeviceSyncEntry] = Field(default_factory=list)


class DeviceStatusBroadcastPayload(BaseModel):
    device_id: str
    status: str
    current_task_id: Optional[str] = None
    current_session_id: Optional[str] = None
    current_run_id: Optional[str] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    timestamp: Optional[str] = None

    @property
    def effective_current_session_id(self) -> Optional[str]:
        return self.current_session_id or self.current_task_id

    @property
    def effective_current_task_id(self) -> Optional[str]:
        return self.current_task_id or self.current_session_id


class LegacyTaskPayload(SessionScopedPayload):
    device_id: str
    instruction: Optional[str] = None
    mode: Optional[str] = None
    max_steps: Optional[int] = None
    max_observe_error_retries: Optional[int] = None

    @property
    def effective_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class RunScopedPayload(SessionScopedPayload):
    device_id: str
    run_started_at: Optional[str] = None


class SessionStatePayload(SessionScopedPayload):
    device_id: str
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    session_run_count: Optional[int] = None


class TransportCorrelationPayload(SessionScopedPayload):
    device_id: str
    version: Optional[int] = None
    ref_msg_id: Optional[str] = None


class ObserveDecisionAppliedPayload(SessionScopedPayload):
    device_id: str
    decision: str
    advice: Optional[str] = None
    success: bool


class PhaseConfirmedPayload(SessionScopedPayload):
    device_id: str
    approved: bool


class TaskInterruptedPayload(SessionScopedPayload):
    device_id: str
    reason: Optional[str] = None
    data: dict = Field(default_factory=dict)


class SessionSnapshotPayload(SessionScopedPayload):
    device_id: str
    status: Optional[str] = None
    instruction: Optional[str] = None
    current_step: int = 0
    max_steps: int = 0
    current_run_id: Optional[str] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    session_run_count: Optional[int] = None

    @property
    def effective_current_session_id(self) -> str:
        return self.session_id or self.task_id


class ChatHistoryEntryPayload(SessionScopedPayload):
    id: str
    role: str
    content: str
    created_at: str
    step_number: Optional[int] = None
    phase: Optional[str] = None
    stage: Optional[str] = None
    data: Optional[dict] = None

    @property
    def effective_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class ObserveRouterPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    version: Optional[int] = None
    result: Optional[str] = None
    success: Optional[bool] = None
    screenshot: Optional[str] = None
    error: Optional[str] = None

    @property
    def effective_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class ActionRouterPayload(SessionScopedPayload):
    device_id: str
    action: dict = Field(default_factory=dict)
    reasoning: str = ""
    step_number: int = 0
    round_version: Optional[int] = None

    @property
    def effective_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class SchedulerTaskPayload(SessionScopedPayload):
    device_id: str
    instruction: str
    mode: str = "normal"
    max_steps: int = 100
    max_observe_error_retries: int = settings.REACT_MAX_OBSERVE_ERROR_RETRIES
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    session_run_count: Optional[int] = None

    @property
    def effective_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class SessionAliasPayload(SessionScopedPayload):
    @property
    def task_compat_id(self) -> str:
        return self.task_id or self.effective_session_id


class SessionRunEnvelope(SessionScopedPayload):
    device_id: str
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    session_run_count: Optional[int] = None

    @property
    def effective_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class CompatibilityTaskPayload(SessionScopedPayload):
    device_id: str

    @property
    def task_alias(self) -> str:
        return self.task_id or self.effective_session_id


class SessionRunMessage(SessionScopedPayload):
    device_id: str
    data: dict = Field(default_factory=dict)

    @property
    def task_alias(self) -> str:
        return self.task_id or self.effective_session_id


class PayloadWithSessionRun(SessionScopedPayload):
    device_id: str
    data: dict = Field(default_factory=dict)

    @property
    def task_alias(self) -> str:
        return self.task_id or self.effective_session_id


class SessionCompatPayload(SessionScopedPayload):
    device_id: str

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class RunBoundaryPayload(SessionScopedPayload):
    device_id: str
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    session_run_count: Optional[int] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class DeviceProgressPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    phase: str
    stage: str
    message: str
    version: Optional[int] = None
    success: Optional[bool] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    data: dict = Field(default_factory=dict)

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class DeviceStatusTransportPayload(BaseModel):
    device_id: str
    status: str
    current_task_id: Optional[str] = None
    current_session_id: Optional[str] = None
    current_run_id: Optional[str] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    timestamp: Optional[str] = None

    @property
    def compat_task_id(self) -> Optional[str]:
        return self.current_task_id or self.current_session_id


class ObserveTransportPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    screenshot: Optional[str] = None
    result: str = ""
    success: bool = True
    error: Optional[str] = None
    version: Optional[int] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class ActionTransportPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    action: dict = Field(default_factory=dict)
    reasoning: str = ""

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class ScreenshotRequestTransportPayload(SessionScopedPayload):
    type: Literal["request_screenshot"] = "request_screenshot"
    device_id: str
    step_number: int = 0
    phase: str = "observe"
    purpose: str = "bootstrap"

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class TaskLifecyclePayload(SessionScopedPayload):
    device_id: str
    status: str
    message: Optional[str] = None
    data: dict = Field(default_factory=dict)

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class ObserveErrorLifecyclePayload(SessionScopedPayload):
    device_id: str
    decision: str
    advice: Optional[str] = None
    success: Optional[bool] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class PhaseLifecyclePayload(SessionScopedPayload):
    device_id: str
    approved: bool

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class TaskCreationPayload(SessionScopedPayload):
    device_id: str
    instruction: Optional[str] = None
    mode: Optional[str] = None
    status: str = "pending"
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    session_run_count: Optional[int] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class DeviceSyncTransportPayload(BaseModel):
    devices: list[dict] = Field(default_factory=list)


class DeviceTransportPayload(BaseModel):
    device_id: str
    status: str
    current_task_id: Optional[str] = None
    current_session_id: Optional[str] = None
    current_run_id: Optional[str] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    timestamp: Optional[str] = None

    @property
    def compat_task_id(self) -> Optional[str]:
        return self.current_task_id or self.current_session_id


class ObserveCompatPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    result: str
    success: bool
    screenshot: Optional[str] = None
    error: Optional[str] = None
    version: Optional[int] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class ActionCompatPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    action: dict = Field(default_factory=dict)
    reasoning: str = ""

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class BootstrapRequestPayload(SessionScopedPayload):
    type: Literal["request_screenshot"] = "request_screenshot"
    device_id: str
    step_number: int = 0
    phase: str = "observe"
    purpose: str = "bootstrap"

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class CanonicalStatusPayload(SessionScopedPayload):
    device_id: str
    status: str
    message: str
    data: dict = Field(default_factory=dict)

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class CanonicalProgressPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    phase: str
    stage: str
    message: str
    version: Optional[int] = None
    data: dict = Field(default_factory=dict)

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class CanonicalStepPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    reasoning: str = ""
    action: dict = Field(default_factory=dict)
    result: str = ""
    screenshot: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    error_type: Optional[str] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class DeviceMetadataPayload(BaseModel):
    device_id: str
    current_task_id: Optional[str] = None
    current_session_id: Optional[str] = None
    current_run_id: Optional[str] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None

    @property
    def compat_task_id(self) -> Optional[str]:
        return self.current_task_id or self.current_session_id


class SchedulerObservePayload(SessionScopedPayload):
    device_id: str
    step_number: int
    version: Optional[int] = None
    screenshot: Optional[str] = None
    result: str = ""
    success: bool = True
    error: Optional[str] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class SchedulerActionPayload(SessionScopedPayload):
    device_id: str
    step_number: int
    action: dict = Field(default_factory=dict)
    reasoning: str = ""
    round_version: Optional[int] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class SessionRunStatePayload(SessionScopedPayload):
    device_id: str
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    session_run_count: Optional[int] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class RunAwareObserveDecisionPayload(SessionScopedPayload):
    device_id: str
    decision: str
    advice: Optional[str] = None
    success: Optional[bool] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class RunAwarePhasePayload(SessionScopedPayload):
    device_id: str
    approved: bool

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class RunAwareTaskPayload(SessionScopedPayload):
    device_id: str
    instruction: Optional[str] = None
    mode: Optional[str] = None
    max_steps: Optional[int] = None
    max_observe_error_retries: Optional[int] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None
    session_run_count: Optional[int] = None

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class SessionCompatTransportPayload(SessionScopedPayload):
    device_id: str

    @property
    def compat_task_id(self) -> str:
        return self.task_id or self.effective_session_id


class RunAwareDevicePayload(BaseModel):
    device_id: str
    status: str
    current_task_id: Optional[str] = None
    current_session_id: Optional[str] = None
    current_run_id: Optional[str] = None
    session_started_at: Optional[str] = None
    run_started_at: Optional[str] = None


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
