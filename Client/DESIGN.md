# Distributed Client 设计文档

## 目录

- [设备状态机](#设备状态机)
- [Handler设计](#handler设计)
- [轮询设计](#轮询设计)
- [设备指纹](#设备指纹)
- [能力检查](#能力检查)
- [指令格式设计](#指令格式设计)
- [截图管理](#截图管理)
- [网络层设计](#网络层设计)

---

## 设备状态机

### 移动端设备状态

| 状态 | 说明 | 触发条件 |
|------|------|---------|
| **Idle** | 空闲，可接受任务 | 初始化 / 任务执行完毕 / Interrupted 完成后 |
| **Busy** | 正在执行任务 | 收到任务指令后立即进入 |
| **Offline** | 设备不可用 | 轮询检测不到设备时 |

**状态转换：**

```
                    ┌──────────┐
                    │   Idle   │◄──────────────────────┐
                    └────┬─────┘                       │
                         │                            │
              收到任务指令   │ Interrupted 完成         │ 任务结束
                         │                            │
                         ▼                            │
                    ┌──────────┐                     │
                    │   Busy    │────────────────────►│
                    └──────────┘   finished/error     │
                         │                            │
                         │ Interrupted                │
                         ▼                            │
                    ┌──────────┐                     │
                    │   (直接  │ ─────────────────────┘
                    │   进入   │
                    │   Idle)  │
                    └──────────┘

┌──────────────────────────────────────────────────────────────┐
│  检测不到设备 ──────────────────────────────────────────────►│
│                                                               │
│   Idle ──► Offline        Busy ──► Offline                   │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

### 边缘 PC (Client) 状态

| 状态 | 说明 |
|------|------|
| **Online** | WebSocket 连接正常 |
| **Offline** | WebSocket 连接断开 |

### Interrupted 指令

**定义：** 紧急停止指令，可在任何时候接受，不受 Busy 状态限制。

```json
{
    "type": "interrupt",
    "task_id": "task_xxx",      // 可选，指定取消的任务
    "reason": "user_cancelled"  // user_cancelled | system_emergency
}
```

**执行流程：**
```
Server 发送 Interrupt
    ↓
Client 立即响应 ACK
    ↓
停止当前执行（无需等待）
    ↓
保存当前状态（截图、进度）
    ↓
返回 Interrupted 结果
    ↓
切换到 Idle
```

---

## Handler设计

*根据不同的Device(IOS,Android,Harmony)设计不同的操控指令集*
13 个 操作处理方法

| 操作 | handler.py | handler_ios.py |
|------|:----------:|:-------------:|
| launch | ✓ | ✓ |
| tap | ✓ | ✓ |
| type | ✓ | ✓ |
| swipe | ✓ | ✓ |
| back | ✓ | ✓ |
| home | ✓ | ✓ |
| double_tap | ✓ | ✓ |
| long_press | ✓ | ✓ |
| wait | ✓ | ✓ |
| takeover | ✓ | ✓ |
| note | ✓ | ✓ |
| call_api | ✓ | ✓ |
| interact | ✓ | ✓ |

3种不同的调用工具: ADB , HDC , WebDriverAgent

---

## 轮询设计

启动一个轮询线程，线程中启动三个协程，分别每隔3秒钟进行一次轮询
- 检查ADB设备列表
- 检查HDC设备列表
- 检查WebDriverAgent设备列表

**采用工厂模式设计，包含基类以便后续拓展新平台。**

**轮询后对比差异性：**
- 新设备接入 → 触发能力检查 → 上报 Online
- 设备消失 → 上报 Offline
- 设备状态变化 → 上报最新状态

---

## 设备指纹

### 设备识别码设计

| 平台 | 识别码来源 | 说明 |
|------|-----------|------|
| **Android** | `adb devices` 返回的 serialno | USB: 设备序列号；WiFi: `IP:PORT` |
| **HarmonyOS** | `hdc list targets` 返回的 device_id | 同 ADB 逻辑 |
| **iOS** | WDA 连接 URL + device UDID | 格式: `{wda_url}:{udid}` |

### 端识别码（Client 机器指纹）

采用 **硬件特征哈希 + 连接类型** 的方式生成：

```python
import hashlib
import uuid
import platform

def get_client_fingerprint() -> str:
    """
    生成客户端机器指纹

    策略：
    - Windows: 优先使用主板 UUID (wmic baseboard get uuid)
    - Linux/macOS: 使用 /sys/class/dmi/id/product_uuid
    - fallback: 机器名 + 网卡 MAC 地址
    """
    try:
        system = platform.system()

        if system == "Windows":
            import subprocess
            result = subprocess.run(
                ["powershell", "-Command",
                 "(Get-CimInstance -ClassName Win32_BaseBoard).UUID"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                raw = result.stdout.strip()
            else:
                raw = _get_fallback_id()
        else:
            try:
                with open("/sys/class/dmi/id/product_uuid", "r") as f:
                    raw = f.read().strip()
            except FileNotFoundError:
                raw = _get_fallback_id()

        return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()

    except Exception:
        return hashlib.md5(platform.node().encode()).hexdigest()[:16].upper()

def _get_fallback_id() -> str:
    """Fallback: 机器名 + 网卡 MAC"""
    mac = uuid.getnode()
    return f"{platform.node()}-{mac}"
```

### 设备指纹完整结构

```json
{
    "device_fingerprint": {
        "client_id": "A1B2C3D4E5F6",      // 端识别码（16位）
        "platform": "android",           // android | harmonyos | ios
        "device_id": "R5CR12345ABC",     // 设备序列号
        "model": "MI 13 Pro",            // 设备型号（可选）
        "connection": "usb",             // usb | wifi | remote
        "extra": {}                       // 平台特有信息
    }
}
```

---

## 能力检查

### 基类设计

所有设备适配器继承自 `DeviceAdapterBase`，首次接入时自动执行能力检查：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class DeviceCapabilities:
    """设备能力"""
    platform: str                           # android | harmonyos | ios
    screenshot: bool                        # 支持截图
    input_text: bool                        # 支持文本输入
    system_buttons: list[str]               # 支持的系统按钮: back, home, power
    battery: bool                            # 能获取电量
    screen_size: tuple[int, int]            # 屏幕分辨率 (width, height)
    os_version: str                         # 系统版本
    supported_apps: list[str]               # 已安装的可用应用列表
    api_level: Optional[int] = None         # API 级别（Android）
    device_name: Optional[str] = None       # 设备名称


class DeviceAdapterBase(ABC):
    """设备适配器基类"""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self._capabilities: Optional[DeviceCapabilities] = None

    @abstractmethod
    async def check_capabilities(self) -> DeviceCapabilities:
        """
        首次连接时检查，返回设备能力
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """
        心跳检测
        """
        raise NotImplementedError

    @abstractmethod
    def get_screenshot(self) -> bytes:
        """
        获取截图
        """
        raise NotImplementedError

    @abstractmethod
    def execute_action(self, action: dict) -> ActionResult:
        """
        执行动作
        """
        raise NotImplementedError

    @property
    def capabilities(self) -> Optional[DeviceCapabilities]:
        """获取已检查的能力，未检查时返回 None"""
        return self._capabilities

    @property
    def is_available(self) -> bool:
        """设备是否可用"""
        return self._capabilities is not None


class ADBAdapter(DeviceAdapterBase):
    """Android 设备适配器"""

    async def check_capabilities(self) -> DeviceCapabilities:
        # 获取设备信息
        # 获取已安装应用
        # 获取屏幕分辨率
        # 获取系统版本
        # 检查各项能力
        ...

class HDCAdapter(DeviceAdapterBase):
    """HarmonyOS 设备适配器"""

    async def check_capabilities(self) -> DeviceCapabilities:
        ...

class WDAAdapter(DeviceAdapterBase):
    """iOS 设备适配器"""

    async def check_capabilities(self) -> DeviceCapabilities:
        ...
```

### 能力检查时机

| 时机 | 说明 |
|------|------|
| 首次设备接入 | 立即执行完整检查 |
| Client 重连 | 重新检查能力 |
| 定期复查 | 每 10 分钟复查 `supported_apps` |

---

## 指令格式设计

### 消息协议基础

```json
{
    "msg_id": "uuid-v4",         // 消息唯一ID，用于 ACK 确认
    "version": "1.0",             // 协议版本
    "timestamp": "ISO8601"        // 时间戳
}
```

### 原始 AutoGLM 动作格式

原始项目模型的输出格式：
```
do(action="Tap", element={"x": 500, "y": 300})
do(action="Type", text="hello")
do(action="Swipe", start={"x": 500, "y": 300}, end={"x": 500, "y": 600})
do(action="Launch", app="微信")
finish(message="任务完成")
```

解析后的 dict 结构：
```python
{
    "_metadata": "do",      // 或 "finish"
    "action": "Tap",        // 动作类型
    "element": {"x": 500, "y": 300},  // 参数（可选）
    "text": "hello",        // 参数（可选）
}
```

### 指令消息类型

#### 1. 任务下发（Server → Client）

```json
{
    "msg_id": "uuid-xxx",
    "type": "task",
    "version": "1.0",
    "timestamp": "2024-03-15T10:30:00Z",
    "task_id": "task_20240315_001",
    "target": {
        "device_id": "R5CR12345ABC",
        "platform": "android"
    },
    "model_config": {
        "base_url": "http://localhost:8000/v1",
        "model": "autoglm-phone-9b",
        "api_key": "your_api_key"
    },
    "task": "打开微信搜索附近的人",
    "max_steps": 100,
    "priority": 1,
    "timeouts": {
        "step_max_seconds": 60,
        "task_max_seconds": 3600
    },
    "screenshot_config": {
        "upload_on": ["error", "finish", "interrupted", "interval:5"]
    }
}
```

#### 2. Interrupted 指令（Server → Client）

```json
{
    "msg_id": "uuid-xxx",
    "type": "interrupt",
    "version": "1.0",
    "timestamp": "2024-03-15T10:30:00Z",
    "task_id": "task_20240315_001",      // 可选，不指定则停止所有
    "reason": "user_cancelled"            // user_cancelled | system_emergency
}
```

#### 3. 消息确认（Client → Server）

```json
{
    "msg_id": "uuid-xxx",
    "type": "ack",
    "ref_msg_id": "server-msg-id",       // 引用收到的消息ID
    "accepted": true,                    // true | false
    "device_status": "busy",             // 收到任务后立即进入 busy
    "timestamp": "2024-03-15T10:30:01Z",
    "error": null                         // 拒绝原因（如果 accepted=false）
}
```

#### 4. 动作执行（内部格式）

```json
{
    "_metadata": "do",
    "action": "tap",
    "element": {"x": 500, "y": 300},
    "reasoning": "点击搜索按钮进入搜索页面"
}
```

```json
{
    "_metadata": "finish",
    "message": "任务完成，已搜索到附近的人"
}
```

#### 5. 任务状态更新（Client → Server）

```json
{
    "msg_id": "uuid-xxx",
    "type": "task_update",
    "timestamp": "2024-03-15T10:30:10Z",
    "task_id": "task_20240315_001",
    "device_id": "R5CR12345ABC",
    "status": "running",
    "progress": {
        "current_step": 5,
        "max_steps": 100,
        "current_action": "type",
        "current_app": "微信",
        "screenshot_url": "/screenshots/task_20240315_001/step_5.png"
    }
}
```

#### 6. 任务结果（Client → Server）

```json
{
    "msg_id": "uuid-xxx",
    "type": "task_result",
    "timestamp": "2024-03-15T10:30:45Z",
    "task_id": "task_20240315_001",
    "device_id": "R5CR12345ABC",
    "status": "completed",              // completed | error | interrupted | cancelled
    "result": {
        "finish_message": "任务完成，已成功搜索到附近的人",
        "total_steps": 12,
        "duration_seconds": 45,
        "screenshots": [
            "/screenshots/task_20240315_001/step_0.png",
            "/screenshots/task_20240315_001/step_1.png"
        ]
    }
}
```

#### 7. 设备状态上报（Client → Server）

```json
{
    "msg_id": "uuid-xxx",
    "type": "device_status",
    "timestamp": "2024-03-15T10:30:00Z",
    "client_id": "A1B2C3D4E5F6",
    "devices": [
        {
            "device_id": "R5CR12345ABC",
            "platform": "android",
            "status": "idle",            // idle | busy | offline
            "model": "MI 13 Pro",
            "connection": "usb",
            "capabilities": {
                "screenshot": true,
                "input_text": true,
                "system_buttons": ["back", "home"],
                "battery": true,
                "screen_size": [1080, 2400],
                "os_version": "Android 14",
                "supported_apps": ["微信", "支付宝", "淘宝"]
            },
            "current_task_id": null,
            "updated_at": "2024-03-15T10:30:00Z"
        }
    ]
}
```

#### 8. 错误上报（Client → Server）

```json
{
    "msg_id": "uuid-xxx",
    "type": "error",
    "timestamp": "2024-03-15T10:30:15Z",
    "task_id": "task_20240315_001",
    "device_id": "R5CR12345ABC",
    "error": {
        "code": "STEP_TIMEOUT",
        "message": "单步执行超时（60秒）",
        "step": 5,
        "current_action": "tap",
        "stack_trace": null
    }
}
```

#### 9. 心跳（Client → Server）

```json
{
    "msg_id": "uuid-xxx",
    "type": "heartbeat",
    "timestamp": "2024-03-15T10:30:00Z",
    "client_id": "A1B2C3D4E5F6",
    "client_status": "online"             // online | offline
}
```

---

## Action 参数对照表

| Action | 必需参数 | 可选参数 | 说明 |
|--------|---------|---------|------|
| tap | element | message | element = {x, y} 相对坐标 |
| double_tap | element | - | 双击坐标 |
| long_press | element | duration | 长按，默认 1s |
| type | text | - | 文本输入 |
| swipe | start, end | duration | 滑动，duration 默认 300ms |
| launch | app | - | 启动应用（应用名） |
| back | - | - | 返回键 |
| home | - | - | Home 键 |
| wait | duration | - | 等待秒数 |
| takeover | message | - | 请求人工介入 |
| note | - | content | 记录内容 |
| call_api | - | api_name, params | 调用外部 API |
| interact | - | message | 用户交互信号 |

### 坐标系统

所有坐标为 **相对坐标 (0-999)**，执行时乘以实际屏幕分辨率转换为绝对像素：
```
abs_x = element["x"] / 999 * screen_width
abs_y = element["y"] / 999 * screen_height
```

---

## 截图管理

### 配置项

```json
{
    "screenshot_config": {
        "mode": "hybrid",                    // local | upload | hybrid
        "local_path": "./screenshots",
        "upload_on": [                       // 触发上传的时机
            "error",                         // 错误时
            "finish",                        // 完成时
            "interrupted",                    // 中断时
            "interval:5"                     // 每5步上传一次
        ],
        "thumbnail_size": [320, 720],         // 缩略图尺寸
        "compression": "webp",                // 压缩格式
        "quality": 80,                        // 质量 0-100
        "retention_days": 7                   // 本地保留天数
    }
}
```

### 存储策略

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| **local** | 仅本地存储，不上传 | 低带宽环境 |
| **upload** | 全部上传到 Server | 需要实时监控 |
| **hybrid** | 本地+按需上传 | 推荐，平衡监控与性能 |

### 截图命名

```
screenshots/
├── {task_id}/
│   ├── step_0.png
│   ├── step_1.png
│   ├── thumbnail_0.webp
│   ├── thumbnail_1.webp
│   └── final.png
```

---

## 网络层设计

### WebSocket 连接流程

```
Client                              Server
  │                                    │
  │─────── Connect ───────────────────►│
  │                                    │
  │◄─────── Welcome (session_id) ─────│
  │                                    │
  │─────── Register ──────────────────►│  (上报 client_id + capabilities)
  │                                    │
  │◄─────── ACK ───────────────────────│
  │                                    │
  │     双向实时消息交互...              │
  │                                    │
  │─────── Heartbeat (每30s) ────────►│
  │◄─────── Pong ──────────────────────│
  │                                    │
  │─────── Disconnect ────────────────►│
  │                                    │
```

### 消息类型汇总

| Type | Direction | 说明 |
|------|-----------|------|
| `task` | Server → Client | 下发任务 |
| `interrupt` | Server → Client | 中断指令 |
| `ack` | Client → Server | 消息确认 |
| `task_update` | Client → Server | 任务进度更新 |
| `task_result` | Client → Server | 任务结果 |
| `device_status` | Client → Server | 设备状态上报 |
| `error` | Client → Server | 错误上报 |
| `heartbeat` | Client → Server | 心跳保活 |
| `pong` | Server → Client | 心跳响应 |

### 任务接收流程

```
Server 发送 task
    ↓
Client 收到消息，立即发送 ack（accepted + device_status=busy）
    ↓
开始执行任务
    ↓
循环：截图 → 模型推理 → 执行动作 → 上报 task_update
    ↓
任务结束（finished/error/interrupted）
    ↓
上报 task_result
    ↓
device_status = idle
```

### 并发策略

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| `queue` | 任务入队排队 | 串行执行 |
| `replace` | 新任务替换当前任务 | 优先新任务 |
| `reject` | 忙碌时拒绝新任务 | 需要保证任务完整性 |

---

## Dispatcher设计

设计一个调度器，根据设备指纹，将指令发送到不同设备上运行。

### 批量设备消息路由

服务器支持同时对多台设备下达指令，Client 需要正确解析和分发。

#### 批量任务下发（Server → Client）

```json
{
    "msg_id": "uuid-xxx",
    "type": "batch_task",
    "version": "1.0",
    "timestamp": "2024-03-15T10:30:00Z",
    "batch_id": "batch_20240315_001",
    "tasks": [
        {
            "task_id": "task_001",
            "target": {
                "device_id": "R5CR12345ABC",
                "platform": "android"
            },
            "model_config": {...},
            "task": "打开微信",
            "max_steps": 50
        },
        {
            "task_id": "task_002",
            "target": {
                "device_id": "HW-P50-12345",
                "platform": "harmonyos"
            },
            "model_config": {...},
            "task": "打开支付宝",
            "max_steps": 50
        }
    ],
    "dispatch_mode": "parallel"
}
```

#### 批量指令响应（Client → Server）

```json
{
    "msg_id": "uuid-yyy",
    "type": "batch_ack",
    "ref_msg_id": "uuid-xxx",
    "batch_id": "batch_20240315_001",
    "timestamp": "2024-03-15T10:30:01Z",
    "results": [
        {
            "task_id": "task_001",
            "device_id": "R5CR12345ABC",
            "accepted": true,
            "device_status": "busy"
        },
        {
            "task_id": "task_002",
            "device_id": "HW-P50-12345",
            "accepted": false,
            "device_status": "idle",
            "error": "device not found"
        }
    ]
}
```

#### 批量 Interrupt（Server → Client）

```json
{
    "msg_id": "uuid-xxx",
    "type": "batch_interrupt",
    "timestamp": "2024-03-15T10:30:00Z",
    "batch_id": "batch_20240315_001",
    "task_ids": ["task_001", "task_002"],
    "reason": "user_cancelled"
}
```

### 消息路由匹配

```python
def route_message(message: dict) -> list[str]:
    """路由消息到对应设备"""
    if message.get("type") == "batch_task":
        return [task["target"]["device_id"] for task in message["tasks"]]
    elif message.get("target"):
        return [message["target"]["device_id"]]
    else:
        return []
```

---

## 日志审计

### 设计原则

- **本地保留为主**：日志默认存储在 Client 本地
- **按需拉取**：Server 主动询问时才上传
- **批量拉取**：支持按时间范围、设备、任务类型过滤

### 日志本地存储

```json
{
    "log_config": {
        "local_path": "./logs",
        "retention_days": 30,
        "max_size_mb": 1024,
        "level": "INFO"
    }
}
```

### 日志格式

```json
{
    "log_id": "uuid-xxx",
    "timestamp": "2024-03-15T10:30:05.123Z",
    "level": "INFO",
    "source": {
        "client_id": "A1B2C3D4E5F6",
        "device_id": "R5CR12345ABC",
        "task_id": "task_20240315_001"
    },
    "event": "action_executed",
    "data": {
        "action": "tap",
        "element": {"x": 500, "y": 300},
        "success": true,
        "duration_ms": 150
    },
    "tags": ["task_execution", "action"]
}
```

### 日志事件类型

| Event | 说明 |
|-------|------|
| `client_started` | Client 启动 |
| `client_connected` | WebSocket 连接成功 |
| `client_disconnected` | WebSocket 断开 |
| `device_connected` | 设备连接 |
| `device_disconnected` | 设备断开 |
| `device_status_changed` | 设备状态变化 |
| `task_received` | 收到任务 |
| `task_started` | 任务开始执行 |
| `action_executed` | 动作执行 |
| `action_failed` | 动作执行失败 |
| `model_request` | 模型请求 |
| `model_response` | 模型响应 |
| `task_completed` | 任务完成 |
| `task_failed` | 任务失败 |
| `interrupt_received` | 收到中断指令 |
| `error` | 错误发生 |

### Server 拉取日志（Server → Client）

```json
{
    "msg_id": "uuid-xxx",
    "type": "log_request",
    "timestamp": "2024-03-15T10:30:00Z",
    "request_id": "req_20240315_001",
    "filters": {
        "device_ids": ["R5CR12345ABC", "HW-P50-12345"],
        "task_ids": ["task_001"],
        "levels": ["ERROR", "WARNING"],
        "events": ["task_failed", "error"],
        "start_time": "2024-03-15T00:00:00Z",
        "end_time": "2024-03-15T23:59:59Z"
    },
    "pagination": {
        "offset": 0,
        "limit": 1000
    }
}
```

### 日志响应（Client → Server）

```json
{
    "msg_id": "uuid-yyy",
    "type": "log_response",
    "ref_msg_id": "uuid-xxx",
    "request_id": "req_20240315_001",
    "timestamp": "2024-03-15T10:30:05Z",
    "total_count": 156,
    "has_more": true,
    "logs": [
        {
            "log_id": "uuid-001",
            "timestamp": "2024-03-15T10:30:05.123Z",
            "level": "ERROR",
            "source": {...},
            "event": "action_failed",
            "data": {...}
        }
    ]
}
```

### 日志拉取时机

| 场景 | 说明 |
|------|------|
| 实时监控 | Server 在任务执行中实时拉取关键日志 |
| 事后分析 | 任务结束后拉取完整日志 |
| 异常排查 | 设备异常时拉取错误级别日志 |
| 定期审计 | 定时拉取所有日志（可选） |

---