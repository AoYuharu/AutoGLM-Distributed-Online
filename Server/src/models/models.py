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
