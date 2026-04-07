# ReAct 处理文档

## 概述

ReAct (Reason + Act) 是 Server 端的任务执行引擎，通过与 AI 模型交互并控制移动设备来自动完成用户指令。

### 设计目标

1. **简化通信架构**：移除 `device_register` 消息，Client 连接时直接通过 `device_id` 识别
2. **集中状态管理**：Server 端维护 `DeviceStatusManagerTable` 设备状态表
3. **清晰错误处理**：通过回调接口抽象各类错误处理逻辑
4. **可追溯的执行流程**：5 阶段状态机，每个阶段职责明确

---

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT (设备端)                          │
│  • 连接 WS 时直接携带 device_id (Query/Header)                  │
│  • 接收 action_cmd → 执行 → 返回 ack (WS)                        │
│  • 发送 observe_result (HTTP POST)                             │
│  • 发送 device_status (HTTP POST)                             │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┴───────────────────┐
          │                                       │
     WebSocket /ws?device_id=xxx           HTTP POST
          │                                       │
          ▼                                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                         SERVER (服务端)                          │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              DeviceStatusManagerTable                      │  │
│  │  device_id → {status: ok/offline/busy, last_update, ...}  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                      ReActEngine                            │  │
│  │  Reason → ActParse → DeviceStatusCheck → SendAndWaitAck   │  │
│  │                        → WaitObservation                    │  │
│  └────────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                    ActionRouter                             │  │
│  │  send_action() / handle_ack() / handle_observe_result()   │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 状态机

### 5 阶段流程

```
┌─────────┐
│ REASON  │ ──────────────────────────────────────────────────────────┐
└────┬────┘                                                           │
     │ AI API 调用成功                                                │
     ▼                                                                 │
┌─────────────┐                                                       │
│  ACT_PARSE  │ ──────────────────────────────────────────────────────┤
└──────┬──────┘                                                       │
       │ Action 解析成功                                               │ No
       ▼                                                             │
┌───────────────┐                                                     │
│ DEVICE_CHECK  │ ────────────────────────────────────────────────── │ ──► ERROR
└───────┬───────┘                                                     │
        │ Device OK (递增 version_code)                               │
        ▼                                                             │
┌───────────────┐                                                     │
│ SEND_ACK_WAIT │ ◄──────────────────────────────────────────────────┘
└───────┬───────┘
        │ Ack 收到
        ▼
┌───────────────────┐
│ WAIT_OBSERVATION  │ ───────────────────────────────────────────────► DONE
└───────────────────┘
        │ 有效 Screenshot
        ▼
    ┌───────┐
    │ REASON │ ──► 循环
    └───────┘
```

### 状态说明

| 状态 | 说明 |
|------|------|
| `REASON` | 调用 AI API 获取 reasoning 和 action |
| `ACT_PARSE` | 解析 action，失败则带纠错提示重试 |
| `DEVICE_CHECK` | 检查设备状态（查表） |
| `SEND_ACK_WAIT` | 发送 action_cmd 并等待 ack |
| `WAIT_OBSERVATION` | 等待 observe_result |
| `WAITING_AUX` | 等待用户辅助输入 |
| `DONE` | 任务完成 |
| `ERROR` | 发生错误 |

---

## 各阶段详解

### Phase 1: REASON

**职责**：调用 AI 模型获取推理和动作

**流程**：
1. 构建 Prompt（包含系统提示、历史步骤、当前截图）
2. 调用 AI API（超时 10 秒）
3. 解析返回内容，提取 `<response>` 和 `<answer>` 部分
4. 成功则进入 ACT_PARSE 阶段

**重试策略**：
- 最多 3 次重试
- 每次重试间隔 1 秒
- 3 次都失败则触发 `on_api_call_failed` 回调

### Phase 2: ACT_PARSE

**职责**：解析 AI 返回的 action

**流程**：
1. 尝试从 `<answer>` 标签中提取 `do(action=...)` 格式
2. 解析动作类型和参数
3. 验证动作有效性

**重试策略**：
- 最多 3 次重试
- 第 1 次直接解析，第 2-3 次带纠错提示
- 3 次都失败则触发 `on_action_parse_error` 回调

**支持的 Action 类型**：
| Action | 参数 | 说明 |
|--------|------|------|
| `Launch` | `app` | 启动应用 |
| `Tap` | `element=[x,y]` | 点击坐标 |
| `Swipe` | `start=[x1,y1]`, `end=[x2,y2]` | 滑动 |
| `Type` | `text` | 输入文本 |
| `Back` | - | 返回 |
| `Home` | - | 桌面 |
| `Wait` | `duration` | 等待 |
| `finish` | `message` | 完成任务 |

### Phase 3: DEVICE_CHECK

**职责**：检查设备状态

**流程**：
1. 查询 `DeviceStatusManagerTable`
2. 根据状态处理：
   - `OK`：递增 version_code，进入 SEND_ACK_WAIT
   - `BUSY`：触发 `on_device_busy` 回调，进入 ERROR
   - `OFFLINE`：触发 `on_device_offline` 回调，进入 ERROR

### Phase 4: SEND_ACK_WAIT

**职责**：发送 action_cmd 并等待 ack

**流程**：
1. 通过 ActionRouter 发送 `action_cmd` 消息
2. 等待 Client 的 ack 响应
3. 验证 ack 有效性

**重试策略**：
- 最多 3 次重试
- 每次等待 ack 超时 10 秒
- 3 次都无 ack 则触发 `on_device_no_reply` 回调

### Phase 5: WAIT_OBSERVATION

**职责**：等待 Client 返回执行结果

**流程**：
1. 等待 Client 的 `observe_result` HTTP POST
2. 验证结果有效性（success=true, screenshot 长度 > 100）
3. 保存 screenshot 和 observation
4. 进入下一轮 REASON

**超时配置**：
- 默认超时 120 秒（2 分钟）
- 超时触发 `on_observation_timeout` 回调

---

## 错误回调

### 7 种错误类型

| 错误类型 | 触发时机 | 回调方法 |
|----------|----------|----------|
| API 调用失败 | Reason 阶段 3 次重试都失败 | `on_api_call_failed(ctx, error)` |
| Action 解析错误 | ActParse 阶段 3 次重试都失败 | `on_action_parse_error(ctx, raw, err)` |
| 设备离线 | DeviceCheck 发现 OFFLINE | `on_device_offline(ctx)` |
| 设备忙碌 | DeviceCheck 发现 BUSY | `on_device_busy(ctx)` |
| 设备无响应 | SendAckWait 3 次无 ack | `on_device_no_reply(ctx)` |
| 观察超时 | WaitObservation 120 秒超时 | `on_observation_timeout(ctx)` |
| 观察错误 | WaitObservation 无效 screenshot | `on_observation_error(ctx, obs)` |
| 等待用户输入 | 需要用户辅助信息 | `on_waiting_for_user_input(ctx)` |

### 回调接口定义

```python
class ReActCallback(ABC):
    @abstractmethod
    def on_api_call_failed(self, ctx: ReActContext, error: Exception) -> None:
        pass

    @abstractmethod
    def on_action_parse_error(self, ctx: ReActContext, raw_response: str,
                              parse_error: Exception) -> None:
        pass

    @abstractmethod
    def on_device_offline(self, ctx: ReActContext) -> None:
        pass

    @abstractmethod
    def on_device_busy(self, ctx: ReActContext) -> None:
        pass

    @abstractmethod
    def on_device_no_reply(self, ctx: ReActContext) -> None:
        pass

    @abstractmethod
    def on_observation_timeout(self, ctx: ReActContext) -> None:
        pass

    @abstractmethod
    def on_observation_error(self, ctx: ReActContext, observation: dict) -> None:
        pass

    @abstractmethod
    def on_waiting_for_user_input(self, ctx: ReActContext) -> bool:
        """
        请求用户辅助输入。

        Args:
            ctx: 当前 ReAct 上下文

        Returns:
            True 表示将等待用户输入，False 表示直接标记错误
        """
        pass
```

### ReActContext 数据结构

```python
@dataclass
class ReActContext:
    task_id: str          # 任务 ID
    device_id: str        # 设备 ID
    instruction: str      # 用户指令
    step_number: int      # 当前步骤号
    reasoning: str = ""   # AI 推理
    action: Optional[dict] = None  # 当前动作
    error_message: str = ""
    extra_data: dict = None
```

### 自定义回调示例

```python
class MyReActCallbacks(ReActCallback):
    def on_api_call_failed(self, ctx, error):
        # 记录日志，标记任务失败
        logger.error("API call failed", extra=ctx.to_dict())
        # 可选：重新入队任务

    def on_action_parse_error(self, ctx, raw, err):
        # 记录日志，标记任务失败
        logger.error("Action parse error", extra=ctx.to_dict())

    def on_device_offline(self, ctx):
        # 标记设备离线，任务重新入队
        await device_manager.set_offline(ctx.device_id)
        # 可选：任务重新入队

    def on_device_busy(self, ctx):
        # 任务重新入队等待
        logger.warning("Device busy, requeueing", extra=ctx.to_dict())

    def on_device_no_reply(self, ctx):
        # 标记任务失败
        logger.error("Device no reply", extra=ctx.to_dict())

    def on_observation_timeout(self, ctx):
        # 重试或标记失败
        logger.warning("Observation timeout", extra=ctx.to_dict())

    def on_observation_error(self, ctx, obs):
        # 记录日志，继续
        logger.warning("Observation error", extra=ctx.to_dict())
```

---

## DeviceStatusManagerTable

### 表结构

```python
class DeviceStatus(str, Enum):
    OK = "ok"          # 设备空闲，可接受任务
    BUSY = "busy"      # 设备正在执行任务
    OFFLINE = "offline"  # 设备离线

@dataclass
class DeviceStatusEntry:
    device_id: str
    status: DeviceStatus = DeviceStatus.OFFLINE
    last_update: datetime = field(default_factory=datetime.utcnow)
    current_task_id: Optional[str] = None
    version_code: int = 0  # 任务版本号
```

### 核心方法

| 方法 | 说明 |
|------|------|
| `update_status(device_id, status, task_id)` | 更新设备状态 |
| `get_status(device_id)` | 获取设备状态 |
| `set_busy(device_id, task_id)` | 设置设备为忙碌 |
| `set_idle(device_id)` | 设置设备为空闲 |
| `set_offline(device_id)` | 设置设备为离线 |
| `increment_version(device_id)` | 递增版本号并返回 |
| `is_device_ok(device_id)` | 检查设备是否可用 |
| `is_device_offline(device_id)` | 检查设备是否离线 |
| `is_device_busy(device_id)` | 检查设备是否忙碌 |

### 使用场景

**WebSocket 连接时**：
```python
# Client 连接时
await device_status_manager.set_idle(device_id)

# Client 断开时
await device_status_manager.set_offline(device_id)
```

**任务执行时**：
```python
# 开始执行任务
await device_status_manager.set_busy(device_id, task_id)

# 任务完成
await device_status_manager.set_idle(device_id)
```

**任务接收时**：
```python
# 检查设备可用性
if await device_status_manager.is_device_ok(device_id):
    # 发送任务
    pass
else:
    # 设备不可用
    pass
```

---

## 上下文管理 (Context Management)

### 设计目标

1. **维护设备对话上下文**：每个设备维护一个消息列表
2. **支持用户辅助输入**：错误时可暂停等待用户输入
3. **支持任务恢复**：上下文可跨任务保留

### 对话流程

**正常流程**:
```
User + [Assistant1, Assistant2, ...] + SystemPrompt
```

**中断后辅助**:
```
User + [Assistant1, Assistant2] + UserAux(辅助信息) + SystemPrompt
```

### DeviceContext 数据结构

```python
@dataclass
class DeviceContext:
    device_id: str           # 设备 ID
    task_id: Optional[str]    # 任务 ID

    # 对话消息列表 (交替的 user/assistant)
    messages: list[dict]

    # 系统提示词
    system_prompt: str

    # 原始用户指令
    original_instruction: str

    # 当前步骤的 reasoning
    current_reasoning: str

    # 是否在等待用户辅助输入
    waiting_for_aux_input: bool

    # 挂起原因
    pending_reason: Optional[str]
```

### 核心方法

| 方法 | 说明 |
|------|------|
| `create_context(device_id, task_id, instruction, system_prompt)` | 创建设备上下文 |
| `get_context(device_id)` | 获取上下文 |
| `set_waiting(device_id, reason)` | 标记等待辅助输入 |
| `add_aux_input(device_id, aux_content)` | 添加辅助用户输入 |
| `clear_context(device_id)` | 清空上下文 |
| `to_api_messages()` | 转换为 API 消息格式 |

### API 端点

| 端点 | 方法 | 描述 |
|-----|------|-----|
| `/api/v1/context/{device_id}/status` | GET | 获取上下文状态 |
| `/api/v1/context/{device_id}/messages` | GET | 获取完整消息历史 |
| `/api/v1/context/aux_input` | POST | 提交辅助用户输入 |
| `/api/v1/context/{device_id}/context` | DELETE | 清空上下文 |
| `/api/v1/context/list` | GET | 列出所有上下文 |

### 提交辅助输入

```bash
curl -X POST http://localhost:8000/api/v1/context/aux_input \
  -H "Content-Type: application/json" \
  -d '{"device_id": "device_001", "aux_content": "用户提供的额外信息"}'
```

### 获取上下文状态

```bash
curl http://localhost:8000/api/v1/context/device_001/status
```

响应示例:
```json
{
  "device_id": "device_001",
  "exists": true,
  "task_id": "task_xxx",
  "waiting_for_input": true,
  "pending_reason": "设备离线，需要用户确认",
  "turn_count": 3,
  "is_empty": false,
  "message_count": 7,
  "current_reasoning": "..."
}
```

---

## 消息协议

### 4 消息精简协议

| 方向 | 消息类型 | 传输方式 | 说明 |
|------|----------|----------|------|
| C → S | - | WebSocket | Client 连接时携带 device_id |
| S → C | `action_cmd` | WebSocket | 执行动作 |
| C → S | `ack` | WebSocket | 动作已接收 |
| C → S | `observe_result` | HTTP POST | 动作执行结果 |
| C → S | `device_status` | HTTP POST | 设备状态更新 |

### action_cmd (Server → Client)

```json
{
    "msg_id": "uuid-v4",
    "type": "action_cmd",
    "timestamp": "2026-04-03T10:00:00.000Z",
    "version": "1.0",
    "payload": {
        "task_id": "task_xxx",
        "device_id": "10AE551838000D7",
        "step_number": 1,
        "action": {
            "action": "Tap",
            "element": [500, 300]
        },
        "reasoning": "点击微信图标进入聊天"
    }
}
```

### ack (Client → Server)

```json
{
    "msg_id": "action_cmd msg_id",
    "type": "ack",
    "accepted": true
}
```

### observe_result (Client → Server)

```json
{
    "msg_id": "uuid-v4",
    "type": "observe_result",
    "timestamp": "2026-04-03T10:00:05.000Z",
    "version": "1.0",
    "payload": {
        "task_id": "task_xxx",
        "device_id": "10AE551838000D7",
        "step_number": 1,
        "success": true,
        "result": "Successfully tapped at [500, 300]",
        "screenshot": "base64_encoded_screenshot..."
    }
}
```

### device_status (Client → Server)

```json
{
    "msg_id": "uuid-v4",
    "type": "device_status",
    "timestamp": "2026-04-03T10:00:00.000Z",
    "version": "1.0",
    "payload": {
        "devices": [
            {
                "device_id": "10AE551838000D7",
                "status": "idle",
                "platform": "android",
                "model": "Xiaomi 13"
            }
        ]
    }
}
```

---

## 完整任务执行示例

### 1. Client 连接

```
Client: WebSocket /ws?device_id=10AE551838000D7
Server: 接受连接，更新 DeviceStatusManagerTable[10AE551838000D7] = OK
```

### 2. 创建任务

```
Client: POST /api/v1/tasks {device_id: "10AE551838000D7", instruction: "打开微信"}
Server: 创建任务，调用 ReActEngine.run_one_cycle()
```

### 3. 第一轮 ReAct

```
Phase 1 (REASON):
  Server → AI API: prompt with instruction
  AI API → Server: "<response>分析屏幕...</response><answer>do(action="Launch", app="微信")</answer>"

Phase 2 (ACT_PARSE):
  Server: 解析 action = {action: "Launch", app: "微信"}

Phase 3 (DEVICE_CHECK):
  Server: 查询 DeviceStatusManagerTable[10AE551838000D7] = OK
  Server: version_code = 1

Phase 4 (SEND_ACK_WAIT):
  Server → Client: action_cmd {action: {action: "Launch", app: "微信"}}
  Client → Server: ack {accepted: true}

Phase 5 (WAIT_OBSERVATION):
  Client → Server: observe_result {success: true, screenshot: "..."}
  Server: 保存 screenshot，进入下一轮 REASON
```

### 4. 后续轮次

类似步骤 3，持续执行直到：
- AI 返回 `finish` action
- 达到最大步数限制
- 发生错误

### 5. 任务完成

```
Server: 更新 DeviceStatusManagerTable[10AE551838000D7] = OK
Server: 通知客户端任务完成
```

---

## 关键文件清单

| 文件 | 路径 | 说明 |
|------|------|------|
| 设备状态管理 | `src/services/device_status_manager.py` | DeviceStatusManagerTable |
| 设备上下文 | `src/services/device_context.py` | DeviceContext 管理 |
| ReAct Engine | `src/services/react_engine.py` | 5 阶段状态机实现 |
| 错误回调 | `src/services/callbacks.py` | ReActCallback 接口 |
| WS 端点 | `src/api/ws.py` | WebSocket 连接处理 |
| 设备 HTTP | `src/api/devices.py` | device_status 接收 |
| 上下文 API | `src/api/context.py` | 上下文管理 API |
| 观察结果 | `src/api/tasks.py` | observe_result 接收 |
| Action 路由 | `src/services/action_router.py` | 消息路由 |

---

## 验证步骤

1. **启动 Server**
   ```bash
   cd Server
   python -m uvicorn src.main:app --reload
   ```

2. **模拟 Client 连接**
   ```python
   import websockets
   async with websockets.connect("ws://localhost:8000/ws?device_id=test123") as ws:
       # 无需发送 device_register，直接连接
       await ws.send(json.dumps({"type": "heartbeat"}))
   ```

3. **创建任务**
   ```bash
   curl -X POST http://localhost:8000/api/v1/tasks \
     -H "Content-Type: application/json" \
     -d '{"device_id": "test123", "instruction": "打开微信"}'
   ```

4. **观察 ReAct 流程**
   - 查看日志中 `ReAct cycle starting` 记录
   - 确认各阶段执行顺序
   - 验证错误回调触发

5. **模拟错误场景**
   - 断开设备连接 → 验证 `on_device_offline`
   - 设备 busy → 验证 `on_device_busy`
   - AI 返回无效格式 → 验证 `on_action_parse_error`
