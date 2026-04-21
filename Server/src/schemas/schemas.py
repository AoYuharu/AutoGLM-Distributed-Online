"""
Pydantic schemas for API request/response validation
"""
from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field, model_serializer


# ============= Client Schemas =============

class ClientCreate(BaseModel):
    """Schema for creating a client"""
    name: str = Field(..., max_length=255)


class ClientResponse(BaseModel):
    """Schema for client response"""
    id: str
    client_id: str
    name: Optional[str]
    is_active: bool
    last_connected_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class ClientWithKey(ClientResponse):
    """Schema for client response with API key (only on creation)"""
    api_key: str


# ============= Device Schemas =============

class DeviceInfo(BaseModel):
    """Device information"""
    model: Optional[str] = None
    os_version: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None


class DeviceStatusUpdate(BaseModel):
    """Schema for device status update"""
    status: str = Field(..., pattern="^(idle|busy|offline|error)$")
    device_info: Optional[DeviceInfo] = None
    current_task_id: Optional[str] = None


class DeviceRemarkUpdate(BaseModel):
    """Schema for device remark update"""
    remark: Optional[str] = Field(None, max_length=500)


class DeviceResponse(BaseModel):
    """Schema for device response"""
    id: str
    device_id: str
    client_id: str
    platform: str
    model: Optional[str] = None  # Database field (maps to device_name in API)
    os_version: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    status: str
    connection: Optional[str] = "usb"
    last_seen: Optional[datetime] = None
    current_task_id: Optional[str] = None
    remark: Optional[str] = None  # 设备备注

    # Add computed field for API response
    @property
    def device_name(self) -> Optional[str]:
        """API field - returns model value"""
        return self.model

    # Custom serializer to include device_name
    @model_serializer(mode='wrap')
    def serialize_model(self, handler):
        data = handler(self)
        data['device_name'] = self.model
        return data

    class Config:
        from_attributes = True


class DeviceListResponse(BaseModel):
    """Schema for device list response"""
    devices: List[DeviceResponse]
    total: int
    online: int
    offline: int


class DeviceRegister(BaseModel):
    """Schema for device registration"""
    device_id: Optional[str] = None  # Optional since path param provides it
    platform: str = Field(..., pattern="^(android|harmonyos|ios)$")
    model: Optional[str] = None
    os_version: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    capabilities: Dict[str, Any] = Field(default_factory=dict)


class PendingDeviceResponse(BaseModel):
    """Schema for pending device response"""
    id: str
    device_id: str
    client_id: Optional[str]
    platform: str
    model: Optional[str]
    os_version: Optional[str]
    screen_width: Optional[int]
    screen_height: Optional[int]
    status: str
    reject_reason: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class PendingDeviceListResponse(BaseModel):
    """Schema for pending device list response"""
    devices: List[PendingDeviceResponse]
    total: int


class PendingDeviceCreate(BaseModel):
    """Schema for creating pending device request"""
    device_id: str
    platform: str = Field(..., pattern="^(android|harmonyos|ios)$")
    model: Optional[str] = None
    os_version: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None


class PendingDeviceApprove(BaseModel):
    """Schema for approving pending device"""
    client_id: Optional[str] = None  # Optional client_id for the approved device


class PendingDeviceReject(BaseModel):
    """Schema for rejecting pending device"""
    reason: Optional[str] = None


# ============= Task Schemas =============

class TaskCreate(BaseModel):
    """Schema for creating a task"""
    device_id: Optional[str] = None
    client_id: Optional[str] = None
    platform: Optional[str] = Field(None, pattern="^(android|harmonyos|ios)$")
    instruction: str = Field(..., min_length=1)
    mode: str = Field("normal", pattern="^(cautious|normal)$")
    max_steps: int = Field(100, ge=1, le=1000)
    max_observe_error_retries: int = Field(2, ge=0, le=20)
    priority: int = Field(1, ge=1, le=10)


class ObserveErrorDecisionRequest(BaseModel):
    """Schema for observe-error retry decision"""
    decision: str = Field(..., pattern="^(continue|interrupt)$")
    advice: Optional[str] = None


class BatchTaskCreate(BaseModel):
    """Schema for creating batch tasks"""
    dispatch_mode: str = Field("parallel", pattern="^(parallel|sequential)$")
    tasks: List[TaskCreate]


class TaskResponse(BaseModel):
    """Schema for task response"""
    id: str
    task_id: str
    device_id: str
    instruction: str
    status: str
    mode: str
    max_steps: int
    current_step: int
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    result: Optional[Dict[str, Any]]
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


class TaskDetailResponse(TaskResponse):
    """Schema for task detail with steps"""
    steps: List["TaskStepResponse"] = []


class TaskListResponse(BaseModel):
    """Schema for task list response"""
    tasks: List[TaskResponse]
    total: int


class ChatMessageResponse(BaseModel):
    """Schema for persisted chat message response"""
    id: str
    role: str
    content: str
    thinking: Optional[str] = None
    action_type: Optional[str] = None
    action_params: Optional[Dict[str, Any]] = None
    screenshot_path: Optional[str] = None
    created_at: datetime
    task_id: Optional[str] = None
    step_number: Optional[int] = None
    phase: Optional[str] = None
    stage: Optional[str] = None
    progress_status_text: Optional[str] = None
    progress_message: Optional[str] = None
    result: Optional[str] = None
    success: Optional[bool] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    version: Optional[int] = None
    error_code: Optional[Any] = None
    data: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class DeviceTaskSessionResponse(BaseModel):
    """Schema for web agent window hydration per device"""
    device_id: str
    task_id: Optional[str] = None
    status: Optional[str] = None
    instruction: Optional[str] = None
    current_step: int = 0
    max_steps: int = 0
    max_observe_error_retries: int = 0
    consecutive_observe_error_count: int = 0
    awaiting_observe_error_decision: bool = False
    pending_observe_error_message: Optional[str] = None
    pending_observe_error_prompt: Optional[Dict[str, Any]] = None
    latest_screenshot: Optional[str] = None
    interruptible: bool = False
    latest_error_reason: Optional[str] = None
    chat_history: List[ChatMessageResponse] = Field(default_factory=list)


class DeviceChatHistoryResponse(BaseModel):
    """Schema for persisted/synthesized chat history hydration"""
    device_id: str
    task_id: Optional[str] = None
    messages: List[ChatMessageResponse] = Field(default_factory=list)
    total: int = 0


# ============= Task Step Schemas =============

class TaskStepResponse(BaseModel):
    """Schema for task step response"""
    id: str
    step_number: int
    action_type: str
    action_params: Dict[str, Any]
    thinking: Optional[str]
    duration_ms: Optional[int]
    success: bool
    error: Optional[str]
    screenshot_url: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ActionDecision(BaseModel):
    """Schema for action decision in cautious mode"""
    action: str = Field(..., pattern="^(confirm|reject|skip)$")
    reason: Optional[str] = None


# ============= Log Schemas =============

class LogEntryCreate(BaseModel):
    """Schema for creating a log entry"""
    timestamp: datetime
    log_type: str
    level: str = Field("info", pattern="^(info|success|warning|error)$")
    message: str
    details: Optional[Dict[str, Any]] = None
    screenshot_url: Optional[str] = None


class LogUploadRequest(BaseModel):
    """Schema for log upload from client"""
    logs: List[LogEntryCreate]
    client_info: Dict[str, str] = Field(default_factory=dict)


class LogEntryResponse(BaseModel):
    """Schema for log entry response"""
    id: str
    device_id: str
    task_id: Optional[str]
    log_type: str
    level: str
    message: str
    details: Optional[Dict[str, Any]]
    screenshot_url: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class LogListResponse(BaseModel):
    """Schema for log list response"""
    logs: List[LogEntryResponse]
    total: int


# ============= WebSocket Message Schemas =============

class WSMessageBase(BaseModel):
    """Base WebSocket message"""
    msg_id: str
    type: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    version: str = "1.0"


class WSAuthMessage(WSMessageBase):
    """WebSocket authentication message"""
    type: str = "auth"
    token: str


class WSAgentCommand(WSMessageBase):
    """WebSocket agent command message"""
    type: str = "agent_command"
    device_id: str
    command: str
    mode: str = "normal"


class WSSubscribeMessage(WSMessageBase):
    """WebSocket subscription message"""
    type: str = "subscribe"
    subscriptions: List[Dict[str, str]]


class WSHeartbeatMessage(WSMessageBase):
    """WebSocket heartbeat message"""
    type: str = "heartbeat"


# ============= Batch Agent Schemas =============

class BatchAgentTaskCreate(BaseModel):
    """Schema for creating batch agent tasks"""
    device_ids: List[str] = Field(..., min_length=1)
    instruction: str = Field(..., min_length=1)
    mode_policy: str = Field("default", pattern="^(force_cautious|force_normal|default)$")
    max_steps: int = Field(50, ge=1, le=100)


class BatchAgentTaskResponse(BaseModel):
    """Schema for batch agent task response"""
    results: List[Dict[str, Any]]


# ============= Response Schemas =============

class ApiResponse(BaseModel):
    """Standard API response"""
    success: bool
    message: str
    data: Optional[Any] = None


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    components: Dict[str, str]
    metrics: Dict[str, Any]


# ============= HTTP Client Message Schemas =============

class DeviceInfoPayload(BaseModel):
    """Device information in HTTP payload"""
    device_id: str
    status: str = "idle"
    platform: Optional[str] = "android"
    # Support both device_name (from Client) and model (legacy)
    device_name: Optional[str] = None
    model: Optional[str] = None
    os_version: Optional[str] = None
    screen_size: Optional[List[int]] = None
    capabilities: Optional[Dict[str, Any]] = None
    current_task_id: Optional[str] = None
    previous_status: Optional[str] = None
    updated_at: Optional[str] = None

    def get_device_name(self) -> Optional[str]:
        """Get device name from either field"""
        return self.device_name or self.model


class DeviceStatusPayload(BaseModel):
    """Payload for device_status HTTP message"""
    devices: List[DeviceInfoPayload]


class DeviceStatusMessage(BaseModel):
    """Full device_status HTTP message"""
    msg_id: str
    type: str = "device_status"
    version: str = "1.0"
    timestamp: Optional[str] = None
    client_id: Optional[str] = None
    payload: DeviceStatusPayload


class ObserveResultPayload(BaseModel):
    """Payload for observe_result HTTP message"""
    task_id: str
    device_id: str
    step_number: int
    screenshot: Optional[str] = None
    result: Optional[str] = ""
    success: bool = True
    error: Optional[str] = None
    version: Optional[int] = None


class ObserveResultMessage(BaseModel):
    """Full observe_result HTTP message"""
    msg_id: str
    type: str = "observe_result"
    version: str = "1.0"
    timestamp: Optional[str] = None
    client_id: Optional[str] = None
    payload: ObserveResultPayload
