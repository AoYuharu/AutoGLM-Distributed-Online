# Distributed Server 设计文档

## 目录

- [系统架构](#系统架构)
- [技术选型](#技术选型)
- [WebSocket 通信设计](#websocket-通信设计)
- [实时性保证](#实时性保证)
- [消息协议](#消息协议)
- [数据库设计](#数据库设计)
- [REST API 设计](#rest-api-设计)
- [前端实时更新](#前端实时更新)
- [任务调度](#任务调度)
- [安全设计](#安全设计)
- [部署架构](#部署架构)

---

## 系统架构

### 整体架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              前端 (Web Client)                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │  设备监控   │  │  任务管理   │  │  日志查看   │  │  实时预览   │        │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │
└─────────┼────────────────┼────────────────┼────────────────┼─────────────────┘
          │                │                │                │
          │   WebSocket    │   WebSocket    │   WebSocket    │   HTTP REST
          ▼                ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              API Gateway                                     │
│                    (WebSocket 升级 / REST 路由 / 认证)                       │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
          ▼                        ▼                        ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│   WebSocket Hub     │  │   REST API Server  │  │   文件服务          │
│   (实时通信核心)     │  │   (任务/设备管理)   │  │   (截图/日志)       │
└─────────┬───────────┘  └─────────┬───────────┘  └─────────┬───────────┘
          │                        │                        │
          └────────────────────────┼────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              数据存储层                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   Redis     │  │  PostgreSQL  │  │    MinIO    │  │   消息队列   │        │
│  │  会话/缓存   │  │   持久数据   │  │   文件存储   │  │  (可选)     │        │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────────────────────┘
                                   ▲
                                   │ HTTP / WebSocket
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
          ▼                        ▼                        ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│   Android Client    │  │  HarmonyOS Client  │  │    iOS Client      │
│   (多个设备)        │  │    (多个设备)      │  │    (多个设备)       │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
```

### 服务分层

| 层次 | 组件 | 职责 |
|------|------|------|
| **接入层** | API Gateway | 认证、WebSocket 升级、路由 |
| **实时层** | WebSocket Hub | 客户端连接管理、消息推送 |
| **业务层** | REST API | 任务 CRUD、设备管理 |
| **数据层** | PostgreSQL | 持久化存储 |
| **缓存层** | Redis | 会话、实时状态、Pub/Sub |
| **文件层** | MinIO/S3 | 截图、日志存储 |

---

## 技术选型

### 后端技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| **语言** | Python 3.10+ / FastAPI | 高性能异步框架 |
| **WebSocket** | FastAPI WebSocket / Socket.IO | 实时双向通信 |
| **数据库** | PostgreSQL + SQLAlchemy | 关系型数据 |
| **缓存** | Redis | 会话、状态、Pub/Sub |
| **文件存储** | MinIO / 本地存储 | 截图、日志 |
| **任务队列** | Redis Queue / Celery | 异步任务 |

### 前端技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| **框架** | React / Vue 3 | 现代化前端框架 |
| **状态管理** | Zustand / Pinia | 轻量级状态管理 |
| **实时通信** | Socket.IO Client | WebSocket 封装 |
| **UI 组件** | Ant Design / Element Plus | 企业级组件库 |
| **实时预览** | Canvas / WebRTC | 截图流式更新 |

---

## WebSocket 通信设计

### 连接管理

#### 1. 连接建立流程

```
Client                              Server
  │                                    │
  │─────── HTTP GET /ws ──────────────►│  (WebSocket 升级请求)
  │         + Token in Header          │
  │                                    │
  │◄─────── 101 Switching Protocols ───│
  │                                    │
  │─────── Auth Message ──────────────►│  {type: "auth", token: "xxx"}
  │                                    │
  │◄─────── Auth Success ───────────────│  {type: "auth_success", user_id: "xxx"}
  │                                    │
  │◄─────── Welcome ────────────────────│  {type: "welcome", session_id: "xxx"}
  │                                    │
```

#### 2. 多设备并发连接

一个浏览器可以同时连接多个 WebSocket 连接：

```javascript
// 前端连接管理
class ConnectionManager {
  constructor() {
    this.connections = new Map(); // connection_id -> WebSocket
    this.eventHandlers = new Map(); // event_type -> [handlers]
  }

  // 建立到特定 Client 的连接
  connectToClient(clientId) {
    const ws = new WebSocket(`ws://server/ws/client/${clientId}`);

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.emit(data.type, data);
    };

    this.connections.set(clientId, ws);
    return ws;
  }

  // 断开连接
  disconnect(clientId) {
    const ws = this.connections.get(clientId);
    if (ws) {
      ws.close();
      this.connections.delete(clientId);
    }
  }
}
```

#### 3. 连接状态管理

```python
@dataclass
class ConnectionState:
    connection_id: str           # 连接唯一ID
    client_id: str              # 客户端ID (前端用户)
    session_id: str             # WebSocket 会话ID
    connected_at: datetime       # 连接时间
    last_heartbeat: datetime     # 最后心跳时间
    subscriptions: list[str]    # 订阅的设备/任务
    is_authenticated: bool      # 是否已认证
```

#### 4. 心跳机制

```python
# 服务端心跳检测
class HeartbeatManager:
    HEARTBEAT_INTERVAL = 30  # 秒
    HEARTBEAT_TIMEOUT = 90   # 超时时间

    async def start_heartbeat_check(self):
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)

            now = datetime.now()
            for conn_id, state in self.connections.items():
                if (now - state.last_heartbeat).seconds > self.HEARTBEAT_TIMEOUT:
                    # 超时断开
                    await self.disconnect(conn_id, reason="heartbeat_timeout")
```

### 消息分类

#### 实时消息 (Server → Client)

| 消息类型 | 说明 | 推送时机 |
|---------|------|---------|
| `device_status_changed` | 设备状态变化 | 设备连接/断开/忙碌 |
| `task_update` | 任务进度更新 | 每个动作执行后 |
| `task_result` | 任务结果 | 任务完成/失败 |
| `screenshot_ready` | 截图就绪 | 截图上传完成 |
| `error_alert` | 错误告警 | 任务执行出错 |
| `log_entry` | 实时日志 | 日志产生时 (可选) |

#### 客户端消息 (Client → Server)

| 消息类型 | 说明 |
|---------|------|
| `subscribe` | 订阅设备/任务更新 |
| `unsubscribe` | 取消订阅 |
| `heartbeat` | 心跳保活 |
| `request_screenshot` | 请求实时截图 |

---

## 实时性保证

### 1. 消息推送延迟目标

| 消息类型 | P50 延迟 | P99 延迟 | 说明 |
|---------|----------|----------|------|
| 任务状态更新 | < 100ms | < 500ms | 动作执行后立即推送 |
| 设备状态变化 | < 200ms | < 1s | 设备连接状态变化 |
| 截图推送 | < 500ms | < 2s | 截图生成到前端展示 |
| 错误告警 | < 100ms | < 300ms | 立即推送 |

### 2. 消息队列设计

```
┌─────────────────────────────────────────────────────────────┐
│                      Redis Pub/Sub                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ device:*    │  │ task:*      │  │ log:*       │        │
│  │ (设备频道)   │  │ (任务频道)   │  │ (日志频道)   │        │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │
│         │               │               │                 │
└─────────┼───────────────┼───────────────┼─────────────────┘
          │               │               │
          ▼               ▼               ▼
┌─────────────────────────────────────────────────────────────┐
│                    WebSocket Hub                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Connection Manager                       │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐              │   │
│  │  │ Conn 1  │  │ Conn 2  │  │ Conn 3  │  ...         │   │
│  │  │ (订阅)   │  │ (订阅)   │  │ (订阅)   │              │   │
│  │  │ 设备A    │  │ 设备A,B  │  │ 任务1    │              │   │
│  │  └─────────┘  └─────────┘  └─────────┘              │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 3. 消息广播策略

```python
class MessageBroadcaster:
    """消息广播器"""

    async def broadcast_device_update(self, device_id: str, update: dict):
        """广播设备更新"""
        # 1. 发布到 Redis 频道
        await self.redis.publish(f"device:{device_id}", json.dumps(update))

        # 2. 直接推送给订阅者
        for connection in self.get_subscribers(f"device:{device_id}"):
            await connection.send_json({
                "type": "device_status_changed",
                "device_id": device_id,
                "data": update,
                "timestamp": datetime.now().isoformat()
            })

    async def broadcast_task_update(self, task_id: str, update: dict):
        """广播任务更新"""
        # 获取任务关联的设备
        task = await self.get_task(task_id)
        device_id = task.device_id

        # 推送给订阅该任务的用户
        for connection in self.get_subscribers(f"task:{task_id}"):
            await connection.send_json({
                "type": "task_update",
                "task_id": task_id,
                "device_id": device_id,
                "data": update,
                "timestamp": datetime.now().isoformat()
            })

        # 同时推送给订阅该设备的用户
        for connection in self.get_subscribers(f"device:{device_id}"):
            await connection.send_json({
                "type": "task_update",
                "task_id": task_id,
                "device_id": device_id,
                "data": update,
                "timestamp": datetime.now().isoformat()
            })
```

### 4. 前端消息处理

```javascript
// 前端 WebSocket 客户端
class RealtimeClient {
  constructor() {
    this.socket = io('/api/ws', {
      transports: ['websocket'],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
    });

    this.setupEventHandlers();
  }

  setupEventHandlers() {
    // 设备状态变化
    this.socket.on('device_status_changed', (data) => {
      store.dispatch('device/updateStatus', data);
      // 触发页面更新
      this.updateDeviceUI(data.device_id, data.data);
    });

    // 任务进度
    this.socket.on('task_update', (data) => {
      store.dispatch('task/updateProgress', data);
      // 实时预览截图
      if (data.data.screenshot_url) {
        this.updateScreenshotPreview(data.task_id, data.data.screenshot_url);
      }
    });

    // 任务完成
    this.socket.on('task_result', (data) => {
      store.dispatch('task/setResult', data);
      // 展示结果
      this.showTaskResult(data);
    });

    // 截图就绪
    this.socket.on('screenshot_ready', (data) => {
      // 流式更新预览图
      this.updateScreenshotPreview(data.task_id, data.url);
    });
  }

  // 订阅特定设备
  subscribeToDevice(deviceId) {
    this.socket.emit('subscribe', { type: 'device', id: deviceId });
  }

  // 订阅任务更新
  subscribeToTask(taskId) {
    this.socket.emit('subscribe', { type: 'task', id: taskId });
  }
}
```

---

## 消息协议

### 消息基础结构

```json
{
    "msg_id": "uuid-v4",
    "type": "message_type",
    "timestamp": "2024-03-15T10:30:00.123Z",
    "version": "1.0"
}
```

### 服务端 → 客户端 (实时推送)

#### 1. 设备状态变化

```json
{
    "msg_id": "uuid-xxx",
    "type": "device_status_changed",
    "timestamp": "2024-03-15T10:30:00.123Z",
    "device_id": "10AE551838000D7",
    "client_id": "CLIENT_001",
    "platform": "android",
    "status": "busy",
    "data": {
        "previous_status": "idle",
        "current_task_id": "task_20240315_001",
        "device_info": {
            "model": "vivo V2324HA",
            "os_version": "Android 16",
            "screen_size": [1080, 2400]
        }
    }
}
```

#### 2. 任务进度更新

```json
{
    "msg_id": "uuid-xxx",
    "type": "task_update",
    "timestamp": "2024-03-15T10:30:05.123Z",
    "task_id": "task_20240315_001",
    "device_id": "10AE551838000D7",
    "status": "running",
    "progress": {
        "current_step": 5,
        "max_steps": 100,
        "current_action": "tap",
        "current_app": "微信",
        "screenshot_url": "/api/screenshots/task_20240315_001/step_5.png",
        "thumbnail_url": "/api/screenshots/task_20240315_001/thumb_5.webp"
    }
}
```

#### 3. 任务结果

```json
{
    "msg_id": "uuid-xxx",
    "type": "task_result",
    "timestamp": "2024-03-15T10:35:00.123Z",
    "task_id": "task_20240315_001",
    "device_id": "10AE551838000D7",
    "status": "completed",
    "result": {
        "finish_message": "成功打开微信并搜索附近的人",
        "total_steps": 12,
        "duration_seconds": 285.5,
        "screenshots": [
            "/api/screenshots/task_20240315_001/step_0.png",
            "/api/screenshots/task_20240315_001/step_1.png"
        ],
        "final_screenshot": "/api/screenshots/task_20240315_001/final.png"
    }
}
```

#### 4. 截图就绪

```json
{
    "msg_id": "uuid-xxx",
    "type": "screenshot_ready",
    "timestamp": "2024-03-15T10:30:06.123Z",
    "task_id": "task_20240315_001",
    "device_id": "10AE551838000D7",
    "step": 5,
    "urls": {
        "full": "/api/screenshots/task_20240315_001/step_5.png",
        "thumbnail": "/api/screenshots/task_20240315_001/thumb_5.webp"
    }
}
```

#### 5. 错误告警

```json
{
    "msg_id": "uuid-xxx",
    "type": "error_alert",
    "timestamp": "2024-03-15T10:32:00.123Z",
    "task_id": "task_20240315_001",
    "device_id": "10AE551838000D7",
    "severity": "error",
    "error": {
        "code": "ELEMENT_NOT_FOUND",
        "message": "未找到目标元素",
        "step": 7,
        "screenshot_url": "/api/screenshots/task_20240315_001/error_7.png"
    }
}
```

### 客户端 → 服务端 (WebSocket)

#### 1. 订阅/取消订阅

```json
{
    "msg_id": "uuid-xxx",
    "type": "subscribe",
    "timestamp": "2024-03-15T10:30:00.123Z",
    "subscriptions": [
        { "type": "device", "id": "10AE551838000D7" },
        { "type": "task", "id": "task_20240315_001" }
    ]
}
```

#### 2. 心跳

```json
{
    "msg_id": "uuid-xxx",
    "type": "heartbeat",
    "timestamp": "2024-03-15T10:30:00.123Z",
    "client_id": "user_001"
}
```

### REST API 消息 (HTTP)

#### 任务下发

```http
POST /api/v1/tasks
Content-Type: application/json
Authorization: Bearer {token}

{
    "target": {
        "client_id": "CLIENT_001",
        "device_id": "10AE551838000D7",
        "platform": "android"
    },
    "model_config": {
        "base_url": "http://model-server:8000/v1",
        "model": "autoglm-phone-9b",
        "api_key": "xxx"
    },
    "task": "打开微信搜索附近的人",
    "max_steps": 100,
    "priority": 1,
    "timeouts": {
        "step_max_seconds": 60,
        "task_max_seconds": 3600
    },
    "screenshot_config": {
        "upload_on": ["finish", "error"],
        "interval": 5
    }
}
```

#### 响应

```json
{
    "task_id": "task_20240315_001",
    "status": "pending",
    "created_at": "2024-03-15T10:30:00.123Z",
    "estimated_start": "2024-03-15T10:30:01.123Z"
}
```

#### 批量任务下发

```http
POST /api/v1/tasks/batch
Content-Type: application/json

{
    "dispatch_mode": "parallel",
    "tasks": [
        {
            "task_id": "task_001",
            "target": {
                "client_id": "CLIENT_001",
                "device_id": "10AE551838000D7"
            },
            "task": "打开设置",
            "max_steps": 50
        },
        {
            "task_id": "task_002",
            "target": {
                "client_id": "CLIENT_002",
                "device_id": "ABCD123456"
            },
            "task": "打开微信",
            "max_steps": 50
        }
    ]
}
```

#### 设备列表

```http
GET /api/v1/devices
```

```json
{
    "devices": [
        {
            "device_id": "10AE551838000D7",
            "client_id": "CLIENT_001",
            "platform": "android",
            "status": "idle",
            "model": "vivo V2324HA",
            "os_version": "Android 16",
            "screen_size": [1080, 2400],
            "connection": "usb",
            "last_seen": "2024-03-15T10:30:00.123Z"
        }
    ],
    "total": 5,
    "online": 3,
    "offline": 2
}
```

---

## 数据库设计

### ER 图

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│    Client     │     │    Device     │     │     Task     │
├──────────────┤     ├──────────────┤     ├──────────────┤
│ id (PK)      │────<│ client_id(FK)│     │ id (PK)      │
│ client_id    │     │ id (PK)       │────<│ device_id(FK)│
│ name         │     │ platform      │     │ status       │
│ api_key      │     │ model         │     │ instruction  │
│ created_at   │     │ status        │     │ result       │
└──────────────┘     │ created_at    │     │ created_at   │
                    └──────────────┘     │ started_at   │
                                         │ finished_at  │
                                         └──────────────┘
                                               │
                                               │
                    ┌──────────────┐     ┌──────────────┐
                    │  TaskStep    │     │   Screenshot │
                    ├──────────────┤     ├──────────────┤
                    │ id (PK)      │     │ id (PK)      │
                    │ task_id(FK)  │────<│ task_id(FK) │
                    │ step_number  │     │ step_number  │
                    │ action       │     │ url          │
                    │ screenshot_id│     │ created_at   │
                    │ created_at   │     └──────────────┘
                    └──────────────┘
```

### 表结构

#### clients

```sql
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255),
    api_key VARCHAR(128) UNIQUE NOT NULL,
    is_active BOOLEAN DEFAULT true,
    last_connected_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_clients_client_id ON clients(client_id);
CREATE INDEX idx_clients_api_key ON clients(api_key);
```

#### devices

```sql
CREATE TABLE devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID REFERENCES clients(id),
    device_id VARCHAR(128) NOT NULL,
    platform VARCHAR(32) NOT NULL,  -- android, harmonyos, ios
    model VARCHAR(128),
    os_version VARCHAR(64),
    screen_width INTEGER,
    screen_height INTEGER,
    status VARCHAR(32) DEFAULT 'offline',  -- online, idle, busy, offline
    capabilities JSONB,
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, device_id)
);

CREATE INDEX idx_devices_client ON devices(client_id);
CREATE INDEX idx_devices_status ON devices(status);
CREATE INDEX idx_devices_device_id ON devices(device_id);
```

#### tasks

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id VARCHAR(128) UNIQUE NOT NULL,
    device_id UUID REFERENCES devices(id),
    client_id UUID REFERENCES clients(id),
    instruction TEXT NOT NULL,
    status VARCHAR(32) DEFAULT 'pending',  -- pending, running, completed, error, interrupted
    priority INTEGER DEFAULT 1,
    max_steps INTEGER DEFAULT 100,
    current_step INTEGER DEFAULT 0,
    result JSONB,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE INDEX idx_tasks_task_id ON tasks(task_id);
CREATE INDEX idx_tasks_device ON tasks(device_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created ON tasks(created_at DESC);
```

#### task_steps

```sql
CREATE TABLE task_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    action VARCHAR(64) NOT NULL,
    action_params JSONB,
    thinking TEXT,
    duration_ms INTEGER,
    success BOOLEAN DEFAULT true,
    screenshot_url VARCHAR(512),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_steps_task ON task_steps(task_id);
CREATE INDEX idx_steps_number ON task_steps(task_id, step_number);
```

#### screenshots

```sql
CREATE TABLE screenshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    step_number INTEGER,
    file_path VARCHAR(512) NOT NULL,
    thumbnail_path VARCHAR(512),
    file_size INTEGER,
    width INTEGER,
    height INTEGER,
    is_final BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_screenshots_task ON screenshots(task_id);
```

---

## REST API 设计

### API 版本

```
/api/v1/...
```

### 认证

| 方式 | 说明 |
|------|------|
| API Key | 客户端认证 `X-API-Key: xxx` |
| JWT Token | 前端用户认证 `Authorization: Bearer xxx` |

### 端点

#### 客户端管理

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/v1/clients` | 注册客户端 |
| GET | `/api/v1/clients` | 获取客户端列表 |
| GET | `/api/v1/clients/{client_id}` | 获取客户端详情 |
| DELETE | `/api/v1/clients/{client_id}` | 删除客户端 |

#### 设备管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/devices` | 获取设备列表 |
| GET | `/api/v1/devices/{device_id}` | 获取设备详情 |
| GET | `/api/v1/devices/{device_id}/screenshot` | 获取最新截图 |

#### 任务管理

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/v1/tasks` | 创建任务 |
| POST | `/api/v1/tasks/batch` | 批量创建任务 |
| GET | `/api/v1/tasks` | 获取任务列表 |
| GET | `/api/v1/tasks/{task_id}` | 获取任务详情 |
| GET | `/api/v1/tasks/{task_id}/steps` | 获取任务步骤 |
| POST | `/api/v1/tasks/{task_id}/interrupt` | 中断任务 |
| DELETE | `/api/v1/tasks/{task_id}` | 删除任务 |

#### 截图和文件

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/screenshots/{task_id}/{filename}` | 获取截图 |
| GET | `/api/v1/logs/{task_id}` | 获取任务日志 |

---

## 前端实时更新

### 页面分区实时性设计

#### 1. 设备监控面板

```
┌─────────────────────────────────────────────────────────────────────┐
│  设备监控                                                       [刷新] │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐        │
│  │ ● vivo-XFold3   │ │ ○ 小米13        │ │ ● Mate60       │        │
│  │   10AE551838...  │ │   离线          │ │   HW-ABC123    │        │
│  │   状态: 空闲     │ │   状态: 离线     │ │   状态: 忙碌    │        │
│  │   Android 16    │ │                 │ │   任务: 任务001  │        │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘        │
└─────────────────────────────────────────────────────────────────────┘

实时更新:
- 设备状态变化立即更新 (WebSocket)
- 设备图标颜色: 绿色=空闲, 黄色=忙碌, 灰色=离线, 红色=错误
- 点击设备展开详情面板
```

#### 2. 任务执行面板

```
┌─────────────────────────────────────────────────────────────────────┐
│  任务执行中: 打开微信搜索附近的人                                      │
│  ┌─────────────────────────────────────────────┐  ┌──────────────┐ │
│  │                                             │  │ 进度: 5/100  │ │
│  │                                             │  │ ████░░░░░░  │ │
│  │            [实时截图预览]                    │  │              │ │
│  │                                             │  │ 当前动作:    │ │
│  │            1920x1080                        │  │ tap          │ │
│  │                                             │  │              │ │
│  │                                             │  │ 当前应用:    │ │
│  │                                             │  │ 微信         │ │
│  │                                             │  │              │ │
│  │                                             │  │ ⏱ 00:45     │ │
│  │                                             │  └──────────────┘ │
│  └─────────────────────────────────────────────┘                   │
│                                                                     │
│  历史步骤:                                                         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ 1. ✓ launch(app="微信")     - 成功                          │  │
│  │ 2. ✓ tap(x=500, y=300)     - 成功                          │  │
│  │ 3. ✓ type(text="附近的人")  - 成功                          │  │
│  │ 4. ✓ tap(x=500, y=600)     - 成功                          │  │
│  │ 5. ⟳ tap(x=500, y=400)     - 执行中...                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘

实时更新:
- 截图每5秒自动刷新 (或每步刷新)
- 进度条实时更新
- 历史步骤列表动态追加
- 当前动作高亮显示
```

#### 3. 日志实时面板

```
┌─────────────────────────────────────────────────────────────────────┐
│  实时日志                                    [暂停] [清空] [导出]       │
├─────────────────────────────────────────────────────────────────────┤
│  🔍 过滤: [全部 ▼] [设备 ▼] [任务 ▼]                                │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ 10:30:05.123  📱 设备连接     10AE551838000D7 vivo-XFold3    │  │
│  │ 10:30:06.456  📋 任务开始     task_001 打开微信               │  │
│  │ 10:30:07.234  🎯 动作执行     tap (500, 300)                 │  │
│  │ 10:30:07.890  ✅ 动作成功     耗时: 150ms                    │  │
│  │ 10:30:08.456  📸 截图上传     step_1.png                     │  │
│  │ 10:30:12.234  🎯 动作执行     type("附近的人")               │  │
│  │ 10:30:13.890  ⚠️  动作失败     超时 60s                      │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘

实时更新:
- 新日志自动追加到顶部
- 不同级别颜色区分: INFO(白), SUCCESS(绿), WARNING(黄), ERROR(红)
- 可暂停/恢复实时流
- 支持过滤和搜索
```

### 前端状态管理

```javascript
// 使用 Zustand 进行状态管理
import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';

const useStore = create(
  subscribeWithSelector((set, get) => ({
    // 设备状态
    devices: {},
    updateDevice: (deviceId, data) => set((state) => ({
      devices: {
        ...state.devices,
        [deviceId]: { ...state.devices[deviceId], ...data }
      }
    })),

    // 任务状态
    tasks: {},
    updateTask: (taskId, data) => set((state) => ({
      tasks: {
        ...state.tasks,
        [taskId]: { ...state.tasks[taskId], ...data }
      }
    })),

    // 截图缓存
    screenshots: {},
    addScreenshot: (taskId, step, url) => set((state) => ({
      screenshots: {
        ...state.screenshots,
        [taskId]: { ...state.screenshots[taskId], [step]: url }
      }
    })),

    // WebSocket 连接
    wsConnected: false,
    setWsConnected: (connected) => set({ wsConnected: connected }),

    // 订阅管理
    subscriptions: new Set(),
    subscribe: (type, id) => set((state) => ({
      subscriptions: new Set([...state.subscriptions, `${type}:${id}`])
    })),
  }))
);

// WebSocket 消息处理
ws.on('device_status_changed', (data) => {
  useStore.getState().updateDevice(data.device_id, data);
  // 如果用户正在查看该设备，更新 UI
  if (currentViewDeviceId === data.device_id) {
    updateDevicePanel(data);
  }
});

ws.on('task_update', (data) => {
  useStore.getState().updateTask(data.task_id, {
    status: data.status,
    progress: data.progress
  });

  // 更新截图
  if (data.progress.screenshot_url) {
    useStore.getState().addScreenshot(
      data.task_id,
      data.progress.current_step,
      data.progress.screenshot_url
    );
  }
});

ws.on('screenshot_ready', (data) => {
  useStore.getState().addScreenshot(data.task_id, data.step, data.urls.full);
  // 触发截图预览组件更新
  screenshotPreviewComponent.refresh(data.task_id, data.step);
});
```

### 实时预览优化

```javascript
// 截图加载优化
class ScreenshotLoader {
  constructor() {
    this.cache = new LRUCache(100);  // LRU 缓存
    this.loading = new Map();  // 正在加载的任务
  }

  async load(taskId, step) {
    const cacheKey = `${taskId}:${step}`;

    // 1. 检查缓存
    if (this.cache.has(cacheKey)) {
      return this.cache.get(cacheKey);
    }

    // 2. 检查是否正在加载
    if (this.loading.has(cacheKey)) {
      return this.loading.get(cacheKey);
    }

    // 3. 发起请求
    const promise = fetch(`/api/screenshots/${taskId}/step_${step}.png`)
      .then(res => res.blob())
      .then(blob => {
        const url = URL.createObjectURL(blob);
        this.cache.set(cacheKey, url);
        this.loading.delete(cacheKey);
        return url;
      });

    this.loading.set(cacheKey, promise);
    return promise;
  }

  // 预加载下一步
  preloadNext(taskId, currentStep) {
    this.load(taskId, currentStep + 1);
  }
}
```

---

## 任务调度

### 调度策略

```python
class TaskScheduler:
    """任务调度器"""

    async def schedule_task(self, task: Task):
        """调度任务"""

        # 1. 选择目标设备
        device = await self.select_device(task)

        if not device:
            # 没有可用设备，任务入队等待
            await self.queue_task(task)
            return

        # 2. 检查设备状态
        if device.status != 'idle':
            # 设备忙碌
            if task.dispatch_mode == 'replace':
                # 替换策略: 中断当前任务
                await self.interrupt_device_task(device)
            elif task.dispatch_mode == 'queue':
                # 排队策略: 加入队列
                await self.queue_task_for_device(task, device)
                return
            else:
                # 拒绝策略
                return TaskResult(status='rejected', reason='device_busy')

        # 3. 下发任务到客户端
        await self.send_task_to_client(device.client, task)

    async def select_device(self, task: Task) -> Optional[Device]:
        """选择目标设备"""
        # 1. 按客户端和设备ID精确匹配
        if task.target_device_id:
            device = await self.get_device(
                client_id=task.client_id,
                device_id=task.target_device_id
            )
            if device and device.status == 'idle':
                return device

        # 2. 按平台选择空闲设备
        if task.target_platform:
            devices = await self.get_idle_devices(platform=task.target_platform)
            if devices:
                return devices[0]

        return None
```

### 任务队列

```python
class TaskQueue:
    """任务队列 (使用 Redis)"""

    async def enqueue(self, task_id: str, priority: int = 1):
        """入队"""
        score = -priority  # Redis ZSET 分数越小越优先
        await self.redis.zadd('task_queue', {task_id: score})

    async def dequeue(self) -> Optional[str]:
        """出队 (最高优先级)"""
        result = await self.redis.zpopmin('task_queue', 1)
        if result:
            return result[0][0]
        return None

    async def requeue(self, task_id: str, delay_seconds: int):
        """延迟重新入队"""
        await asyncio.sleep(delay_seconds)
        await self.enqueue(task_id)
```

---

## 安全设计

### 1. 认证机制

```python
# 客户端认证 (API Key)
class ClientAuth:
    def authenticate(self, request: Request) -> Optional[Client]:
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            return None

        client = self.db.query(Client).filter(
            Client.api_key == api_key,
            Client.is_active == True
        ).first()

        return client

# 前端用户认证 (JWT)
class UserAuth:
    def create_token(self, user_id: str) -> str:
        payload = {
            'sub': user_id,
            'exp': datetime.utcnow() + timedelta(hours=24)
        }
        return jwt.encode(payload, settings.JWT_SECRET)

    def verify_token(self, token: str) -> Optional[str]:
        try:
            payload = jwt.decode(token, settings.JWT_SECRET)
            return payload['sub']
        except jwt.ExpiredSignatureError:
            return None
```

### 2. WebSocket 认证

```python
# WebSocket 连接认证中间件
async def websocket_auth(websocket: WebSocket, token: str = None):
    if not token:
        # 尝试从 query string 获取
        token = websocket.query_params.get('token')

    user_id = user_auth.verify_token(token)
    if not user_id:
        await websocket.close(code=4001, reason='Unauthorized')
        return None

    return user_id
```

### 3. 权限控制

```python
# 前端用户权限
class Permission:
    VIEW_DEVICE = 'view_device'
    CONTROL_DEVICE = 'control_device'
    CREATE_TASK = 'create_task'
    VIEW_LOGS = 'view_logs'

# 检查用户权限
def require_permission(permission: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            user = get_current_user(request)
            if permission not in user.permissions:
                raise PermissionDenied()
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator

@app.post('/api/v1/tasks')
@require_permission(Permission.CREATE_TASK)
async def create_task(request: CreateTaskRequest):
    ...
```

### 4. 速率限制

```python
# API 速率限制
@limiter.limit("100/minute")
async def create_task(request: Request):
    ...

# WebSocket 消息频率限制
class MessageRateLimiter:
    MAX_MESSAGES_PER_SECOND = 10

    async def check_rate(self, connection_id: str) -> bool:
        key = f"rate:{connection_id}"
        count = await self.redis.incr(key)

        if count == 1:
            await self.redis.expire(key, 1)

        return count <= self.MAX_MESSAGES_PER_SECOND
```

---

## 部署架构

### Docker Compose 部署

```yaml
version: '3.8'

services:
  api:
    build: ./server
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/autoglm
      - REDIS_URL=redis://redis:6379
      - MINIO_ENDPOINT=minio:9000
    depends_on:
      - db
      - redis
      - minio
    restart: unless-stopped

  websocket:
    build: ./server
    command: python -m server.ws_server
    ports:
      - "8001:8001"
    environment:
      - REDIS_URL=redis://redis:6379
    depends_on:
      - redis
    restart: unless-stopped

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=autoglm
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    restart: unless-stopped

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      - MINIO_ROOT_USER=minioadmin
      - MINIO_ROOT_PASSWORD=minioadmin
    volumes:
      - minio_data:/data
    restart: unless-stopped

  frontend:
    build: ./frontend
    ports:
      - "3000:80"
    depends_on:
      - api
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  minio_data:
```

### Kubernetes 部署

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: autoglm-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: autoglm-api
  template:
    spec:
      containers:
      - name: api
        image: autoglm/server:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: autoglm-secrets
              key: database-url
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
---
apiVersion: v1
kind: Service
metadata:
  name: autoglm-api
spec:
  type: LoadBalancer
  ports:
  - port: 80
    targetPort: 8000
  selector:
    app: autoglm-api
```

---

## 监控与日志

### 关键指标

| 指标 | 阈值 | 告警 |
|------|------|------|
| WebSocket 连接数 | > 1000 | 警告 |
| 消息延迟 P99 | > 500ms | 警告 |
| 任务失败率 | > 5% | 警告 |
| API 响应时间 | > 1s | 警告 |
| 设备在线率 | < 80% | 警告 |

### 日志收集

```python
# 结构化日志
import structlog

logger = structlog.get_logger()

logger.info(
    "task_step_completed",
    task_id="task_001",
    device_id="device_001",
    step=5,
    action="tap",
    duration_ms=150,
    success=True
)
```

### 健康检查

```http
GET /health
```

```json
{
    "status": "healthy",
    "components": {
        "database": "ok",
        "redis": "ok",
        "minio": "ok"
    },
    "metrics": {
        "websocket_connections": 45,
        "active_tasks": 12,
        "queue_length": 5
    }
}
```

---

## 版本兼容性

### 消息协议版本

| 版本 | 说明 |
|------|------|
| 1.0 | 初始版本 |

### 升级策略

- 消息格式变化添加 `version` 字段
- 旧版本客户端继续支持
- 新版本功能可选

---

## 附录

### A. 错误码

| 错误码 | 说明 |
|--------|------|
| `AUTH_001` | 认证失败 |
| `AUTH_002` | Token 过期 |
| `DEVICE_001` | 设备不在线 |
| `DEVICE_002` | 设备忙碌 |
| `DEVICE_003` | 设备不支持 |
| `TASK_001` | 任务不存在 |
| `TASK_002` | 任务执行失败 |
| `TASK_003` | 任务超时 |
| `TASK_004` | 任务被中断 |

### B. 配置项

```yaml
# config.yaml
server:
  host: 0.0.0.0
  port: 8000
  workers: 4

websocket:
  heartbeat_interval: 30
  heartbeat_timeout: 90
  max_connections: 10000

database:
  url: postgresql://user:pass@localhost:5432/autoglm
  pool_size: 20
  max_overflow: 10

redis:
  url: redis://localhost:6379
  pool_size: 10

storage:
  type: minio  # minio | local
  endpoint: localhost:9000
  bucket: autoglm

logging:
  level: INFO
  format: json
```
