"""
Database models using SQLAlchemy with SQLite
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

from sqlalchemy import (
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    JSON,
)
from sqlalchemy.orm import relationship, declarative_base

from src.database import Base


class DeviceStatus(str, Enum):
    """Device status enum"""
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"


class DevicePlatform(str, Enum):
    """Device platform enum"""
    ANDROID = "android"
    HARMONYOS = "harmonyos"
    IOS = "ios"


class TaskStatus(str, Enum):
    """Task status enum"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class AgentMode(str, Enum):
    """Agent execution mode"""
    CAUTIOUS = "cautious"
    NORMAL = "normal"


def generate_uuid() -> str:
    """Generate a UUID string"""
    return str(uuid.uuid4())


class Client(Base):
    """Client model - represents a client application"""
    __tablename__ = "clients"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    client_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255))
    api_key = Column(String(128), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    last_connected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    devices = relationship("Device", back_populates="client", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="client", cascade="all, delete-orphan")


class Device(Base):
    """Device model - represents a connected phone"""
    __tablename__ = "devices"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    client_id = Column(String(36), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    device_id = Column(String(128), nullable=False, index=True)
    platform = Column(String(32), nullable=False)
    model = Column(String(128))
    os_version = Column(String(64))
    screen_width = Column(Integer)
    screen_height = Column(Integer)
    status = Column(String(32), default=DeviceStatus.OFFLINE.value, index=True)
    capabilities = Column(JSON, default=dict)
    current_task_id = Column(String(128), nullable=True)
    remark = Column(Text, nullable=True)  # 设备备注
    last_heartbeat = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    client = relationship("Client", back_populates="devices")
    tasks = relationship("Task", back_populates="device", cascade="all, delete-orphan")
    task_steps = relationship("TaskStep", back_populates="device", cascade="all, delete-orphan")

    @property
    def last_seen(self):
        """Alias for last_heartbeat for API compatibility"""
        return self.last_heartbeat

    __table_args__ = (
        # Unique constraint per client_id + device_id
        # Note: SQLite doesn't support named constraints well, handled in app logic
    )


class PendingDevice(Base):
    """Pending device model - devices waiting for approval"""
    __tablename__ = "pending_devices"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    device_id = Column(String(128), nullable=False, index=True)
    client_id = Column(String(36), nullable=True)  # 申请者客户端ID
    platform = Column(String(32), nullable=False)
    model = Column(String(128))
    os_version = Column(String(64))
    screen_width = Column(Integer)
    screen_height = Column(Integer)
    status = Column(String(32), default="pending", index=True)  # pending, approved, rejected
    reject_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Task(Base):
    """Task model - represents an automation task"""
    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_id = Column(String(128), unique=True, nullable=False, index=True)
    device_id = Column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(String(36), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    instruction = Column(Text, nullable=False)
    status = Column(String(32), default=TaskStatus.PENDING.value, index=True)
    mode = Column(String(32), default=AgentMode.NORMAL.value)
    priority = Column(Integer, default=1)
    max_steps = Column(Integer, default=100)
    current_step = Column(Integer, default=0)
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    # Relationships
    device = relationship("Device", back_populates="tasks")
    client = relationship("Client", back_populates="tasks")
    steps = relationship("TaskStep", back_populates="task", cascade="all, delete-orphan")
    screenshots = relationship("Screenshot", back_populates="task", cascade="all, delete-orphan")


class TaskStep(Base):
    """Task step model - represents each action in a task"""
    __tablename__ = "task_steps"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    device_id = Column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    step_number = Column(Integer, nullable=False)
    action_type = Column(String(64), nullable=False)
    action_params = Column(JSON, default=dict)
    thinking = Column(Text)
    duration_ms = Column(Integer)
    success = Column(Boolean, default=True)
    error = Column(Text, nullable=True)
    screenshot_url = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    task = relationship("Task", back_populates="steps")
    device = relationship("Device", back_populates="task_steps")


class Screenshot(Base):
    """Screenshot model - stores task screenshots"""
    __tablename__ = "screenshots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    step_number = Column(Integer, nullable=True)
    file_path = Column(String(512), nullable=False)
    thumbnail_path = Column(String(512), nullable=True)
    file_size = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    is_final = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    task = relationship("Task", back_populates="screenshots")


class LogEntry(Base):
    """Log entry model - stores device and task logs"""
    __tablename__ = "log_entries"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    device_id = Column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    task_id = Column(String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    log_type = Column(String(64), nullable=False)
    level = Column(String(32), default="info")
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)
    screenshot_url = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    device = relationship("Device")
    task = relationship("Task")


class ChatMessage(Base):
    """Chat message model - stores conversation history per device"""
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    device_id = Column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(16), nullable=False)  # "user" or "agent"
    content = Column(Text, nullable=False)
    thinking = Column(Text, nullable=True)
    action_type = Column(String(64), nullable=True)
    action_params = Column(JSON, nullable=True)
    screenshot_path = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    device = relationship("Device", back_populates="chat_messages")


# Add relationship to Device
Device.chat_messages = relationship("ChatMessage", back_populates="device", cascade="all, delete-orphan")
