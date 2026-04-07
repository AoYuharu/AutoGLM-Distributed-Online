# Distributed Client TDD 开发流程

## 概述

本文档描述基于 DESIGN.md 设计的 Client 端开发流程，采用 TDD（测试驱动开发）规范。Client 需要支持 Android (ADB)、HarmonyOS (HDC)、iOS (WDA) 三种平台设备的管理和任务执行。

---

## 项目结构

```
Distributed/Client/
├── src/
│   ├── __init__.py
│   ├── main.py                    # 入口文件
│   ├── adapters/                  # 设备适配器模块
│   │   ├── __init__.py
│   │   ├── base.py                 # 基类 DeviceAdapterBase
│   │   ├── adb_adapter.py          # ADB 适配器
│   │   ├── hdc_adapter.py          # HDC 适配器
│   │   └── wda_adapter.py          # WDA 适配器
│   ├── polling/                    # 轮询模块
│   │   ├── __init__.py
│   │   ├── base.py                 # 轮询基类
│   │   ├── factory.py              # 轮询工厂
│   │   └── manager.py              # 轮询管理器
│   ├── state/                      # 状态机模块
│   │   ├── __init__.py
│   │   ├── device_state.py         # 设备状态
│   │   ├── client_state.py         # Client 状态
│   │   └── machine.py              # 状态机实现
│   ├── network/                    # 网络层模块
│   │   ├── __init__.py
│   │   ├── websocket.py             # WebSocket 客户端
│   │   ├── messages.py             # 消息定义与序列化
│   │   └── dispatcher.py           # 消息分发器
│   ├── executor/                   # 任务执行模块
│   │   ├── __init__.py
│   │   ├── task_executor.py        # 任务执行器
│   │   ├── action_handler.py       # Action 处理 (参考原 phone_agent)
│   │   └── interpreter.py          # 动作解析
│   ├── screenshot/                 # 截图管理模块
│   │   ├── __init__.py
│   │   ├── manager.py              # 截图管理器
│   │   └── storage.py              # 存储策略
│   └── logging/                    # 日志模块
│       ├── __init__.py
│       ├── logger.py               # 日志记录器
│       └── audit.py                # 审计日志
├── tests/                          # 测试目录
│   ├── __init__.py
│   ├── conftest.py                 # pytest 配置
│   ├── unit/                       # 单元测试
│   │   ├── test_adapters/
│   │   ├── test_state/
│   │   ├── test_network/
│   │   └── test_executor/
│   └── integration/                # 集成测试
│       └── test_full_flow.py
├── pyproject.toml
├── requirements.txt
└── README.md
```

---

## TDD 开发流程

### 原则

1. **红-绿-重构循环**：先写失败测试 → 写最小实现通过测试 → 重构优化
2. **Mock 外围依赖**：ADB/HDC 命令、网络请求使用 mock
3. **测试独立性**：每个测试可独立运行，不依赖外部环境
4. **覆盖率要求**：核心模块覆盖率 ≥ 90%

---

## 阶段一：设备适配器模块 (Adapters)

### 1.1 测试先行 - DeviceAdapterBase

**文件**: `tests/unit/test_adapters/test_base.py`

```python
import pytest
from unittest.mock import Mock, AsyncMock
from dataclasses import asdict

# 测试代码 - 先写测试
class TestDeviceAdapterBase:
    """测试设备适配器基类"""

    def test_capabilities_returns_none_initially(self):
        """初始状态能力检查返回 None"""
        # Given: 一个新的适配器实例
        # When: 获取能力
        # Then: 返回 None
        pass

    def test_is_available_false_when_no_capabilities(self):
        """未检查能力时设备不可用"""
        pass

    @pytest.mark.asyncio
    async def test_check_capabilities_raises_not_implemented(self):
        """基类 check_capabilities 必须被子类实现"""
        pass

    @pytest.mark.asyncio
    async def test_health_check_raises_not_implemented(self):
        """基类 health_check 必须被子类实现"""
        pass

    def test_get_screenshot_raises_not_implemented(self):
        """基类 get_screenshot 必须被子类实现"""
        pass

    def test_execute_action_raises_not_implemented(self):
        """基类 execute_action 必须被子类实现"""
        pass
```

**实现代码**: `src/adapters/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class DeviceCapabilities:
    """设备能力"""
    platform: str
    screenshot: bool
    input_text: bool
    system_buttons: list[str]
    battery: bool
    screen_size: tuple[int, int]
    os_version: str
    supported_apps: list[str]
    api_level: Optional[int] = None
    device_name: Optional[str] = None


class DeviceAdapterBase(ABC):
    """设备适配器基类"""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self._capabilities: Optional[DeviceCapabilities] = None

    @abstractmethod
    async def check_capabilities(self) -> DeviceCapabilities:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_screenshot(self) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def execute_action(self, action: dict) -> "ActionResult":
        raise NotImplementedError

    @property
    def capabilities(self) -> Optional[DeviceCapabilities]:
        return self._capabilities

    @property
    def is_available(self) -> bool:
        return self._capabilities is not None
```

### 1.2 测试先行 - ADBAdapter

**文件**: `tests/unit/test_adapters/test_adb_adapter.py`

```python
import pytest
from unittest.mock import Mock, patch, MagicMock
from dataclasses import asdict

# 参照 phone_agent/adb/connection.py 和 phone_agent/adb/device.py

class TestADBAdapter:
    """ADB 适配器测试"""

    @pytest.fixture
    def mock_subprocess(self):
        """Mock subprocess 模块"""
        with patch("src.adapters.adb_adapter.subprocess") as mock:
            yield mock

    @pytest.fixture
    def mock_config(self):
        """Mock 配置模块"""
        with patch("src.adapters.adb_adapter.APP_PACKAGES") as mock:
            mock.return_value = {"微信": "com.tencent.mm"}
            yield mock

    # --- 能力检查测试 ---
    @pytest.mark.asyncio
    async def test_check_capabilities_returns_all_info(self, mock_subprocess):
        """检查能力返回完整信息"""
        # Setup mocks
        mock_subprocess.run.return_value = Mock(
            stdout="product:MI 13\nmodel:MI 13 Pro\n",
            returncode=0
        )
        mock_subprocess.check_output.return_value = (
            b"com.tencent.mm\ncom.alipay.mp\n"
        )
        # Execute & Assert
        pass

    @pytest.mark.asyncio
    async def test_check_capabilities_parses_screen_size(self, mock_subprocess):
        """正确解析屏幕分辨率"""
        pass

    @pytest.mark.asyncio
    async def test_check_capabilities_detects_installed_apps(self, mock_subprocess):
        """检测已安装应用"""
        pass

    # --- 健康检查测试 ---
    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_device_online(self, mock_subprocess):
        """设备在线时健康检查返回 True"""
        pass

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_device_offline(self, mock_subprocess):
        """设备离线时健康检查返回 False"""
        pass

    # --- 截图测试 ---
    def test_get_screenshot_returns_bytes(self, mock_subprocess):
        """截图返回字节数据"""
        pass

    def test_get_screenshot_timeout_handling(self, mock_subprocess):
        """截图超时处理"""
        pass

    # --- 动作执行测试 ---
    def test_execute_tap_action(self, mock_subprocess):
        """执行 Tap 动作"""
        pass

    def test_execute_swipe_action(self, mock_subprocess):
        """执行 Swipe 动作"""
        pass

    def test_execute_type_action(self, mock_subprocess):
        """执行 Type 动作"""
        pass

    def test_execute_launch_action(self, mock_subprocess):
        """执行 Launch 动作"""
        pass

    def test_execute_unknown_action_returns_error(self, mock_subprocess):
        """未知动作返回错误"""
        pass
```

**实现代码**: `src/adapters/adb_adapter.py`

```python
import subprocess
from typing import Optional
from src.adapters.base import DeviceAdapterBase, DeviceCapabilities

# 参照原 phone_agent/adb/ 实现

class ADBAdapter(DeviceAdapterBase):
    """Android 设备适配器"""

    def __init__(self, device_id: str, adb_path: str = "adb"):
        super().__init__(device_id)
        self.adb_path = adb_path

    def _run_adb(self, args: list, **kwargs):
        """运行 ADB 命令"""
        cmd = [self.adb_path]
        if self.device_id:
            cmd.extend(["-s", self.device_id])
        cmd.extend(args)
        return subprocess.run(cmd, **kwargs)

    @property
    def _adb_prefix(self) -> list:
        """ADB 命令前缀"""
        prefix = [self.adb_path]
        if self.device_id:
            prefix.extend(["-s", self.device_id])
        return prefix

    async def check_capabilities(self) -> DeviceCapabilities:
        """检查设备能力"""
        # 获取设备型号
        # 获取屏幕分辨率
        # 获取系统版本
        # 获取已安装应用
        # 检查各项能力
        pass

    async def health_check(self) -> bool:
        """心跳检测"""
        pass

    def get_screenshot(self) -> bytes:
        """获取截图"""
        pass

    def execute_action(self, action: dict) -> "ActionResult":
        """执行动作"""
        pass
```

### 1.3 HDCAdapter 和 WDAAdapter

类似 ADBAdapter，参照 `phone_agent/hdc/` 和 `phone_agent/xctest/` 实现。

---

## 阶段二：轮询模块 (Polling)

### 2.1 测试先行 - 轮询基类

**文件**: `tests/unit/test_polling/test_base.py`

```python
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from dataclasses import asdict

class TestPollingBase:
    """轮询基类测试"""

    def test_polling_starts_with_interval(self):
        """轮询按指定间隔执行"""
        pass

    def test_polling_detects_new_devices(self):
        """检测到新设备接入"""
        pass

    def test_polling_detects_disconnected_devices(self):
        """检测到设备断开"""
        pass

    def test_polling_can_be_stopped(self):
        """轮询可以停止"""
        pass

    def test_polling_reports_device_changes(self):
        """上报设备变化"""
        pass


class TestPollingFactory:
    """轮询工厂测试"""

    def test_create_adb_polling(self):
        """创建 ADB 轮询器"""
        pass

    def test_create_hdc_polling(self):
        """创建 HDC 轮询器"""
        pass

    def test_create_wda_polling(self):
        """创建 WDA 轮询器"""
        pass

    def test_unknown_type_raises_error(self):
        """未知类型抛出异常"""
        pass
```

### 2.2 测试先行 - 轮询管理器

**文件**: `tests/unit/test_polling/test_manager.py`

```python
class TestPollingManager:
    """轮询管理器测试"""

    def test_manager_starts_all_pollers(self):
        """管理器启动所有轮询器"""
        pass

    def test_manager_reports_device_events(self):
        """管理器上报设备事件"""
        pass

    def test_manager_handles_device_state_changes(self):
        """处理设备状态变化"""
        pass

    def test_manager_periodic_report(self):
        """定期上报设备状态"""
        pass
```

**实现代码**: `src/polling/factory.py`

```python
from abc import ABC, abstractmethod
from enum import Enum
from typing import Callable
import threading
import time

class PlatformType(Enum):
    ADB = "adb"
    HDC = "hdc"
    WDA = "wda"

class PollingFactory:
    """轮询器工厂"""

    @staticmethod
    def create_polling(
        platform: PlatformType,
        on_device_found: Callable,
        on_device_lost: Callable,
        interval: float = 3.0
    ) -> "BasePolling":
        """创建轮询器"""
        if platform == PlatformType.ADB:
            return ADBPolling(on_device_found, on_device_lost, interval)
        elif platform == PlatformType.HDC:
            return HDCPolling(on_device_found, on_device_lost, interval)
        elif platform == PlatformType.WDA:
            return WDAPolling(on_device_found, on_device_lost, interval)
        else:
            raise ValueError(f"Unknown platform: {platform}")


class BasePolling(ABC):
    """轮询基类"""

    def __init__(
        self,
        on_device_found: Callable,
        on_device_lost: Callable,
        interval: float = 3.0
    ):
        self.on_device_found = on_device_found
        self.on_device_lost = on_device_lost
        self.interval = interval
        self._running = False
        self._thread = None
        self._known_devices: set = set()

    def start(self):
        """启动轮询"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止轮询"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _poll_loop(self):
        """轮询循环"""
        while self._running:
            self._check_devices()
            time.sleep(self.interval)

    @abstractmethod
    def _list_devices(self) -> list[dict]:
        """列出设备"""
        raise NotImplementedError

    def _check_devices(self):
        """检查设备变化"""
        current_devices = {d["device_id"] for d in self._list_devices()}

        # 新设备
        for device_id in current_devices - self._known_devices:
            self.on_device_found(device_id)

        # 消失的设备
        for device_id in self._known_devices - current_devices:
            self.on_device_lost(device_id)

        self._known_devices = current_devices
```

---

## 阶段三：状态机模块 (State)

### 3.1 测试先行

**文件**: `tests/unit/test_state/test_device_state.py`

```python
import pytest
from src.state.device_state import DeviceStatus, DeviceState

class TestDeviceState:
    """设备状态测试"""

    def test_initial_status_is_idle(self):
        """初始状态为空闲"""
        state = DeviceState("device_001", "android")
        assert state.status == DeviceStatus.IDLE

    def test_transition_idle_to_busy_on_task(self):
        """收到任务时从空闲转为忙碌"""
        pass

    def test_transition_busy_to_idle_on_complete(self):
        """任务完成时从忙碌转为空闲"""
        pass

    def test_transition_busy_to_idle_on_error(self):
        """任务错误时从忙碌转为空闲"""
        pass

    def test_transition_to_offline_when_unavailable(self):
        """设备不可用时转为离线"""
        pass

    def test_transition_offline_to_idle_on_reconnect(self):
        """设备重连时从离线转为空闲"""
        pass

    def test_interrupted_cancels_current_task(self):
        """Interrupted 指令取消当前任务"""
        pass

    def test_invalid_transition_raises_error(self):
        """非法状态转换抛出异常"""
        pass


class TestClientState:
    """Client 状态测试"""

    def test_initial_state_is_online(self):
        """初始状态为在线"""
        pass

    def test_websocket_disconnect_sets_offline(self):
        """WebSocket 断开设置离线状态"""
        pass

    def test_websocket_reconnect_sets_online(self):
        """WebSocket 重连设置在线状态"""
        pass
```

**实现代码**: `src/state/device_state.py`

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

class DeviceStatus(Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"

class StateTransitionError(Exception):
    """状态转换错误"""
    pass

@dataclass
class DeviceState:
    """设备状态"""
    device_id: str
    platform: str
    status: DeviceStatus = DeviceStatus.IDLE
    current_task_id: Optional[str] = None
    last_update: datetime = field(default_factory=datetime.now)

    # 有效状态转换
    VALID_TRANSITIONS = {
        DeviceStatus.IDLE: {DeviceStatus.BUSY, DeviceStatus.OFFLINE},
        DeviceStatus.BUSY: {DeviceStatus.IDLE, DeviceStatus.OFFLINE},
        DeviceStatus.OFFLINE: {DeviceStatus.IDLE, DeviceStatus.OFFLINE},
    }

    def transition(self, new_status: DeviceStatus, task_id: Optional[str] = None) -> None:
        """状态转换"""
        if new_status not in self.VALID_TRANSITIONS.get(self.status, set()):
            raise StateTransitionError(
                f"Cannot transition from {self.status.value} to {new_status.value}"
            )

        old_status = self.status
        self.status = new_status
        self.last_update = datetime.now()

        if new_status == DeviceStatus.IDLE:
            self.current_task_id = None
        elif new_status == DeviceStatus.BUSY and task_id:
            self.current_task_id = task_id

    def receive_task(self, task_id: str) -> None:
        """接收任务"""
        self.transition(DeviceStatus.BUSY, task_id)

    def complete_task(self) -> None:
        """完成任务"""
        self.transition(DeviceStatus.IDLE)

    def fail_task(self) -> None:
        """任务失败"""
        self.transition(DeviceStatus.IDLE)

    def interrupt(self) -> None:
        """中断任务"""
        if self.status == DeviceStatus.BUSY:
            self.transition(DeviceStatus.IDLE)

    def device_lost(self) -> None:
        """设备丢失"""
        self.transition(DeviceStatus.OFFLINE)

    def device_recovered(self) -> None:
        """设备恢复"""
        self.transition(DeviceStatus.IDLE)
```

---

## 阶段四：网络层模块 (Network)

### 4.1 测试先行 - 消息定义

**文件**: `tests/unit/test_network/test_messages.py`

```python
import pytest
import json
from src.network.messages import (
    MessageType,
    BaseMessage,
    TaskMessage,
    InterruptMessage,
    AckMessage,
    TaskUpdateMessage,
    TaskResultMessage,
    DeviceStatusMessage,
    ErrorMessage,
    HeartbeatMessage,
)

class TestMessageSerialization:
    """消息序列化测试"""

    def test_task_message_serialization(self):
        """任务消息序列化"""
        msg = TaskMessage(
            msg_id="uuid-xxx",
            task_id="task_001",
            platform="android",
            device_id="device_001",
            task="打开微信",
            max_steps=100,
        )
        data = msg.to_dict()
        assert data["type"] == "task"
        assert data["task_id"] == "task_001"

    def test_task_message_deserialization(self):
        """任务消息反序列化"""
        data = {
            "msg_id": "uuid-xxx",
            "type": "task",
            "task_id": "task_001",
            "target": {"device_id": "device_001", "platform": "android"},
            "task": "打开微信",
            "max_steps": 100,
        }
        msg = TaskMessage.from_dict(data)
        assert msg.task_id == "task_001"

    def test_ack_message_serialization(self):
        """ACK 消息序列化"""
        pass

    def test_device_status_message_serialization(self):
        """设备状态消息序列化"""
        pass

    def test_invalid_message_type_raises_error(self):
        """无效消息类型抛出异常"""
        pass
```

**实现代码**: `src/network/messages.py`

```python
from dataclasses import dataclass, asdict
from typing import Optional, Any
from datetime import datetime
from enum import Enum
import json
import uuid

class MessageType(Enum):
    TASK = "task"
    INTERRUPT = "interrupt"
    ACK = "ack"
    TASK_UPDATE = "task_update"
    TASK_RESULT = "task_result"
    DEVICE_STATUS = "device_status"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    BATCH_TASK = "batch_task"
    BATCH_INTERRUPT = "batch_interrupt"

@dataclass
class BaseMessage:
    """基础消息"""
    msg_id: str
    version: str = "1.0"

    def to_dict(self) -> dict:
        """转换为字典"""
        data = asdict(self)
        data["timestamp"] = datetime.now().isoformat() + "Z"
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BaseMessage":
        """从字典创建"""
        return cls(**data)

    def to_json(self) -> str:
        """转换为 JSON"""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "BaseMessage":
        """从 JSON 创建"""
        return cls.from_dict(json.loads(json_str))

@dataclass
class TaskMessage(BaseMessage):
    """任务消息"""
    type: str = "task"
    task_id: str = ""
    target: dict = None
    platform: str = ""
    device_id: str = ""
    model_config: dict = None
    task: str = ""
    max_steps: int = 100
    priority: int = 1
    timeouts: dict = None
    screenshot_config: dict = None

    def __post_init__(self):
        if self.target is None:
            self.target = {"device_id": self.device_id, "platform": self.platform}

@dataclass
class InterruptMessage(BaseMessage):
    """中断指令"""
    type: str = "interrupt"
    task_id: Optional[str] = None
    reason: str = "user_cancelled"  # user_cancelled | system_emergency

@dataclass
class AckMessage(BaseMessage):
    """消息确认"""
    type: str = "ack"
    ref_msg_id: str = ""
    accepted: bool = True
    device_status: str = "idle"
    error: Optional[str] = None

# ... 其他消息类型类似实现
```

### 4.2 测试先行 - WebSocket 客户端

**文件**: `tests/unit/test_network/test_websocket.py`

```python
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from src.network.websocket import WebSocketClient, ConnectionState

class TestWebSocketClient:
    """WebSocket 客户端测试"""

    @pytest.fixture
    def mock_websocket(self):
        with patch("src.network.websocket.websockets") as mock:
            yield mock

    @pytest.mark.asyncio
    async def test_connect_sends_registration(self, mock_websocket):
        """连接成功发送注册"""
        pass

    @pytest.mark.asyncio
    async def test_send_message_with_ack(self, mock_websocket):
        """发送消息等待 ACK"""
        pass

    @pytest.mark.asyncio
    async def test_receive_task_message(self, mock_websocket):
        """接收任务消息"""
        pass

    @pytest.mark.asyncio
    async def test_receive_interrupt_message(self, mock_websocket):
        """接收中断消息"""
        pass

    @pytest.mark.asyncio
    async def test_heartbeat_sent_periodically(self, mock_websocket):
        """定期发送心跳"""
        pass

    @pytest.mark.asyncio
    async def test_reconnect_on_disconnect(self, mock_websocket):
        """断开后重连"""
        pass

    @pytest.mark.asyncio
    async def test_reconnect_on_connection_error(self, mock_websocket):
        """连接错误时重连"""
        pass
```

**实现代码**: `src/network/websocket.py`

```python
import asyncio
import json
import logging
from typing import Callable, Optional
from dataclasses import asdict
from datetime import datetime
import websockets
from websockets.exceptions import ConnectionClosed

from src.network.messages import (
    BaseMessage,
    MessageType,
    AckMessage,
    HeartbeatMessage,
)

logger = logging.getLogger(__name__)

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"

class WebSocketClient:
    """WebSocket 客户端"""

    def __init__(
        self,
        server_url: str,
        client_id: str,
        on_message: Callable[[dict], None],
        heartbeat_interval: int = 30,
        reconnect_interval: int = 5,
        max_reconnect_attempts: int = 10,
    ):
        self.server_url = server_url
        self.client_id = client_id
        self.on_message = on_message
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_interval = reconnect_interval
        self.max_reconnect_attempts = max_reconnect_attempts

        self._state = ConnectionState.DISCONNECTED
        self._websocket = None
        self._receive_task = None
        self._heartbeat_task = None
        self._running = False
        self._pending_acks: dict[str, asyncio.Future] = {}

    async def connect(self) -> bool:
        """连接到服务器"""
        try:
            self._state = ConnectionState.CONNECTING
            self._websocket = await websockets.connect(self.server_url)

            # 接收欢迎消息
            welcome = await self._websocket.recv()
            session_id = json.loads(welcome).get("session_id")

            # 发送注册
            await self._send_registration(session_id)

            self._state = ConnectionState.CONNECTED
            self._running = True

            # 启动接收和心跳任务
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            return True

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self._state = ConnectionState.DISCONNECTED
            return False

    async def disconnect(self):
        """断开连接"""
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()

        if self._websocket:
            await self._websocket.close()

        self._state = ConnectionState.DISCONNECTED

    async def send_message(self, message: BaseMessage, wait_ack: bool = True) -> bool:
        """发送消息"""
        if self._state != ConnectionState.CONNECTED:
            return False

        try:
            data = message.to_dict()
            await self._websocket.send(json.dumps(data))

            if wait_ack and message.msg_id:
                # 等待 ACK
                future = asyncio.get_event_loop().create_future()
                self._pending_acks[message.msg_id] = future

                try:
                    ack = await asyncio.wait_for(future, timeout=10)
                    return ack.get("accepted", False)
                except asyncio.TimeoutError:
                    return False
                finally:
                    self._pending_acks.pop(message.msg_id, None)

            return True

        except Exception as e:
            logger.error(f"Send message failed: {e}")
            return False

    async def _receive_loop(self):
        """接收消息循环"""
        while self._running:
            try:
                message = await self._websocket.recv()
                data = json.loads(message)

                msg_type = data.get("type")

                if msg_type == "ack":
                    # 处理 ACK
                    ref_id = data.get("ref_msg_id")
                    if ref_id in self._pending_acks:
                        self._pending_acks[ref_id].set_result(data)
                else:
                    # 其他消息传递给回调
                    self.on_message(data)

            except ConnectionClosed:
                logger.warning("WebSocket closed")
                break
            except Exception as e:
                logger.error(f"Receive error: {e}")

        if self._running:
            await self._reconnect()

    async def _heartbeat_loop(self):
        """心跳循环"""
        while self._running:
            await asyncio.sleep(self.heartbeat_interval)
            heartbeat = HeartbeatMessage(
                msg_id=str(uuid.uuid4()),
                client_id=self.client_id,
                client_status="online",
            )
            await self.send_message(heartbeat, wait_ack=False)

    async def _reconnect(self):
        """重连"""
        self._state = ConnectionState.RECONNECTING
        attempts = 0

        while self._running and attempts < self.max_reconnect_attempts:
            attempts += 1
            logger.info(f"Reconnecting... attempt {attempts}")

            if await self.connect():
                return

            await asyncio.sleep(self.reconnect_interval)

        logger.error("Max reconnect attempts reached")
        self._running = False
```

---

## 阶段五：任务执行模块 (Executor)

### 5.1 测试先行 - 任务执行器

**文件**: `tests/unit/test_executor/test_task_executor.py`

```python
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from src.executor.task_executor import TaskExecutor, TaskStatus

class TestTaskExecutor:
    """任务执行器测试"""

    @pytest.fixture
    def mock_adapter(self):
        adapter = Mock()
        adapter.get_screenshot.return_value = b"fake_screenshot"
        adapter.capabilities.screen_size = (1080, 2400)
        adapter.execute_action.return_value = Mock(success=True, should_finish=False)
        return adapter

    @pytest.fixture
    def mock_model_client(self):
        client = Mock()
        client.inference.return_value = '{"action": "Tap", "element": {"x": 500, "y": 300}}'
        return client

    async def test_executor_starts_task(self, mock_adapter, mock_model_client):
        """执行器开始任务"""
        pass

    async def test_executor_captures_screenshot_each_step(self, mock_adapter, mock_model_client):
        """每步都截图"""
        pass

    async def test_executor_sends_screenshot_to_model(self, mock_adapter, mock_model_client):
        """发送截图到模型"""
        pass

    async def test_executor_executes_action(self, mock_adapter, mock_model_client):
        """执行动作"""
        pass

    async def test_executor_stops_on_finish(self, mock_adapter, mock_model_client):
        """finish 动作停止任务"""
        pass

    async def test_executor_stops_on_max_steps(self, mock_adapter, mock_model_client):
        """达到最大步数停止"""
        pass

    async def test_executor_handles_interrupted(self, mock_adapter, mock_model_client):
        """处理中断指令"""
        pass

    async def test_executor_reports_progress(self, mock_adapter, mock_model_client):
        """上报进度"""
        pass

    async def test_executor_handles_action_error(self, mock_adapter, mock_model_client):
        """处理动作执行错误"""
        pass
```

**实现代码**: `src/executor/task_executor.py`

```python
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable
from enum import Enum

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    INTERRUPTED = "interrupted"

@dataclass
class TaskContext:
    """任务上下文"""
    task_id: str
    device_id: str
    instruction: str
    max_steps: int = 100
    current_step: int = 0
    status: TaskStatus = TaskStatus.PENDING
    start_time: datetime = field(default_factory=datetime.now)
    screenshots: list[str] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    last_error: Optional[str] = None

class TaskExecutor:
    """任务执行器"""

    def __init__(
        self,
        adapter,  # DeviceAdapterBase
        model_client,  # ModelClient
        on_progress: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ):
        self.adapter = adapter
        self.model_client = model_client
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_error = on_error

        self._current_task: Optional[TaskContext] = None
        self._running = False
        self._interrupted = False

    async def execute_task(
        self,
        task_id: str,
        device_id: str,
        instruction: str,
        model_config: dict,
        max_steps: int = 100,
    ) -> TaskContext:
        """执行任务"""
        self._running = True
        self._interrupted = False

        self._current_task = TaskContext(
            task_id=task_id,
            device_id=device_id,
            instruction=instruction,
            max_steps=max_steps,
        )

        try:
            self._current_task.status = TaskStatus.RUNNING

            # 循环执行
            while self._running and not self._interrupted:
                if self._current_task.current_step >= max_steps:
                    self._current_task.last_error = "Max steps reached"
                    self._current_task.status = TaskStatus.ERROR
                    break

                # 1. 截图
                screenshot = self.adapter.get_screenshot()
                screenshot_path = self._save_screenshot(screenshot, task_id, self._current_task.current_step)
                self._current_task.screenshots.append(screenshot_path)

                # 2. 发送到模型
                response = await self.model_client.inference(
                    screenshot=screenshot,
                    instruction=instruction,
                    history=self._current_task.actions,
                    **model_config
                )

                # 3. 解析动作
                action = self._parse_action(response)

                # 4. 检查是否结束
                if action.get("_metadata") == "finish":
                    self._current_task.status = TaskStatus.COMPLETED
                    break

                # 5. 执行动作
                result = self.adapter.execute_action(action)
                self._current_task.actions.append(action)

                if not result.success:
                    self._current_task.last_error = result.message
                    self._current_task.status = TaskStatus.ERROR
                    break

                # 6. 上报进度
                if self.on_progress:
                    await self.on_progress(self._current_task)

                self._current_task.current_step += 1

                # 短暂等待页面响应
                await asyncio.sleep(0.5)

            if self._interrupted:
                self._current_task.status = TaskStatus.INTERRUPTED

        except Exception as e:
            logger.error(f"Task execution error: {e}")
            self._current_task.status = TaskStatus.ERROR
            self._current_task.last_error = str(e)

        finally:
            if self.on_complete:
                await self.on_complete(self._current_task)

            return self._current_task

    def interrupt(self, reason: str = "user_cancelled"):
        """中断任务"""
        logger.info(f"Interrupting task: {reason}")
        self._interrupted = True

    def _save_screenshot(self, screenshot: bytes, task_id: str, step: int) -> str:
        """保存截图"""
        # 实现截图保存逻辑
        pass

    def _parse_action(self, response: str) -> dict:
        """解析模型响应"""
        # 参照 phone_agent/actions/handler.py 的 parse_action 实现
        pass
```

### 5.2 Action Handler

参照 `phone_agent/actions/handler.py` 实现，复用现有逻辑。

---

## 阶段六：截图管理模块 (Screenshot)

### 6.1 测试先行

**文件**: `tests/unit/test_screenshot/test_manager.py`

```python
import pytest
from unittest.mock import Mock, patch, mock_open
from src.screenshot.manager import ScreenshotManager, StorageMode

class TestScreenshotManager:
    """截图管理器测试"""

    def test_local_storage_saves_to_disk(self):
        """本地模式保存到磁盘"""
        pass

    def test_upload_storage_sends_to_server(self):
        """上传模式发送到服务器"""
        pass

    def test_hybrid_mode_saves_local_and_upload_on_trigger(self):
        """混合模式按触发器上传"""
        pass

    def test_compression_to_webp(self):
        """压缩为 WebP 格式"""
        pass

    def test_thumbnail_generation(self):
        """生成缩略图"""
        pass

    def test_cleanup_old_files(self):
        """清理过期文件"""
        pass
```

---

## 阶段七：日志模块 (Logging)

### 7.1 测试先行

**文件**: `tests/unit/test_logging/test_logger.py`

```python
import pytest
from src.logging.logger import AuditLogger, EventType

class TestAuditLogger:
    """审计日志测试"""

    def test_log_client_started(self):
        """记录客户端启动"""
        pass

    def test_log_device_connected(self):
        """记录设备连接"""
        pass

    def test_log_task_received(self):
        """记录收到任务"""
        pass

    def test_log_action_executed(self):
        """记录动作执行"""
        pass

    def test_log_task_completed(self):
        """记录任务完成"""
        pass

    def test_filter_by_level(self):
        """按级别过滤"""
        pass

    def test_filter_by_device_id(self):
        """按设备 ID 过滤"""
        pass

    def test_filter_by_time_range(self):
        """按时间范围过滤"""
        pass
```

---

## 阶段八：集成测试

### 8.1 完整流程测试

**文件**: `tests/integration/test_full_flow.py`

```python
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from src.main import DistributedClient

class TestFullFlow:
    """完整流程集成测试"""

    @pytest.mark.asyncio
    async def test_client_startup_and_device_discovery(self):
        """客户端启动和设备发现"""
        pass

    @pytest.mark.asyncio
    async def test_task_execution_flow(self):
        """任务执行完整流程"""
        pass

    @pytest.mark.asyncio
    async def test_interrupt_during_execution(self):
        """执行中中断"""
        pass

    @pytest.mark.asyncio
    async def test_batch_task_execution(self):
        """批量任务执行"""
        pass

    @pytest.mark.asyncio
    async def test_device_reconnection(self):
        """设备重连"""
        pass
```

---

## 开发顺序建议

| 阶段 | 模块 | 优先级 | 依赖 |
|------|------|--------|------|
| 1 | 设备适配器 (Adapters) | P0 | 无 |
| 2 | 状态机 (State) | P0 | 无 |
| 3 | 消息定义 (Messages) | P0 | 无 |
| 4 | 网络层 (Network) | P0 | 3 |
| 5 | 轮询模块 (Polling) | P1 | 1, 2 |
| 6 | 任务执行器 (Executor) | P1 | 1, 3, 4 |
| 7 | 截图管理 (Screenshot) | P2 | 无 |
| 8 | 日志模块 (Logging) | P2 | 无 |
| 9 | 集成测试 | P1 | 1-8 |
| 10 | 主程序入口 (Main) | P0 | 1-9 |

---

## Mock 策略

### ADB/HDC 命令 Mock

```python
@pytest.fixture
def mock_adb_commands():
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.check_output") as mock_check:
        mock_run.return_value = Mock(stdout="", returncode=0)
        mock_check.return_value = b"com.tencent.mm\n"
        yield {"run": mock_run, "check_output": mock_check}
```

### WebSocket Mock

```python
@pytest.fixture
def mock_websocket():
    with patch("websockets.connect") as mock_connect:
        websocket = AsyncMock()
        mock_connect.return_value = websocket
        yield websocket
```

### 文件系统 Mock

```python
@pytest.fixture
def mock_filesystem(tmp_path):
    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    return {"screenshots": screenshots_dir, "logs": logs_dir}
```

---

## 覆盖率要求

| 模块 | 最低覆盖率 |
|------|------------|
| adapters/ | 90% |
| state/ | 95% |
| network/ | 85% |
| polling/ | 90% |
| executor/ | 85% |
| screenshot/ | 80% |
| logging/ | 80% |
| **整体** | **85%** |

---

## 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行单元测试
pytest tests/unit/ -v

# 运行集成测试
pytest tests/integration/ -v

# 带覆盖率
pytest tests/ --cov=src --cov-report=html

# 只运行特定模块
pytest tests/unit/test_adapters/ -v

# TDD 模式：监视文件变化并自动运行
pytest tests/ --watch
```

---

## 注意事项

1. **测试环境隔离**：每个测试使用独立的 mock，避免状态污染
2. **异步测试**：使用 `pytest-asyncio` 处理异步代码
3. **Fixtures 复用**：提取公共 fixtures 到 `conftest.py`
4. **参数化测试**：使用 `@pytest.mark.parametrize` 测试多种场景
5. **Mock 真实外部依赖**：ADB、WDA 等命令必须 mock
6. **边界条件测试**：测试超时、错误处理、异常情况
