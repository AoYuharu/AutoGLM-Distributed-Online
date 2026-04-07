# ReAct 线程池调度器设计

## 1. 设计目标

- 服务器启动线程池，支持配置核心线程数和最大线程数
- 每个 device 会话作为独立任务，轮流执行
- 以 **ReAct 为单位**进行轮转调度

## 2. 核心概念

### ReAct 循环
```
Reason（AI推理）+ Act（执行动作）+ Observe（获取截图）= 一轮
```

### 线程池
- 核心线程数、最大线程数可配置
- 每条线程独立工作，互不等待

### 调度规则
- 以 **一轮 ReAct** 为最小调度单位
- 每执行完一轮 ReAct，device 放回队列**尾部**
- 线程永远从队列**头部**取最新的等待任务

### 队列状态
```
初始队列：[device1, device2, device3, device4, device5]

线程A取走device1
线程B取走device2

线程A完成device1一轮ReAct ──→ 队列变成 [device3, device4, device5, device1]
线程B完成device2一轮ReAct ──→ 队列变成 [device4, device5, device1, device2]

线程A取走device3
线程B取走device4
...
```

## 3. 单个设备任务类

```python
@dataclass
class DeviceTask:
    """单个设备任务"""
    device_id: str
    task_id: str
    instruction: str
    mode: str = "normal"  # normal / cautious

    # 上下文（可截断/恢复）
    context: list[dict] = field(default_factory=list)  # 对话历史

    # ReAct记录
    react_records: list = field(default_factory=list)  # 每轮的Reason/Act/Observe

    # 状态
    phase: str = "idle"  # idle / reason / act / observe
    status: str = "pending"  # pending / running / waiting_confirm / completed / interrupted

    # 进度
    current_step: int = 0
    max_steps: int = 100

    # 时间戳
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
```

## 4. 设备任务上下文类

```python
@dataclass
class DeviceTaskContext:
    """设备任务上下文"""
    system_prompt: str = ""
    messages: list[dict] = field(default_factory=list)

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})

    def truncate(self, keep_last: int = 10):
        """截断旧消息，保留最近N条"""
        if len(self.messages) > keep_last:
            self.messages = [self.messages[0]] + self.messages[-keep_last:]

    def to_api_format(self) -> list[dict]:
        """转换为API格式"""
        result = [{"role": "system", "content": self.system_prompt}]
        result.extend(self.messages)
        return result
```

## 5. ReAct 记录类

```python
@dataclass
class ReActRecord:
    """单次 ReAct 执行记录"""
    step_number: int
    reasoning: str = ""       # AI思考过程
    action: dict = field(default_factory=dict)  # AI决定的动作
    action_result: str = ""   # 动作执行结果
    observation: str = ""     # 观察结果
    screenshot: str = ""      # 截图base64
    success: bool = True
```

## 6. 任务完成条件

满足以下任一条件则任务完成：
1. AI 返回 `finish` 动作
2. 执行步数达到 `max_steps`
3. 用户主动中断

## 7. 调度器类

```python
class ReActScheduler:
    def __init__(
        self,
        core_threads: int = 4,
        max_threads: int = 8,
        reason_timeout: int = 30,
        observe_timeout: int = 10
    ):
        self.executor = ThreadPoolExecutor(
            max_workers=max_threads,
            thread_name_prefix="react_worker_"
        )
        self._task_queue: list[str] = []  # device_id 队列
        self._queue_lock = Lock()
        self._device_tasks: dict[str, DeviceTask] = {}

    def submit_task(self, device_id: str, task_id: str, instruction: str):
        """提交新任务到队列尾部"""
        # ...

    def get_next_task(self) -> str | None:
        """从队列头部取任务"""
        # ...

    def requeue_task(self, device_id: str):
        """任务完成一轮后放回队列尾部"""
        # ...
```

## 8. Worker 线程执行逻辑

```python
class ReActWorker:
    """每个线程执行一个 ReAct 循环"""

    def run_one_cycle(self, device_id: str) -> bool:
        """
        执行一个 ReAct 循环
        Returns: True=任务完成/需切换, False=继续当前任务
        """

        # Phase 1: Reason - 调用AI模型
        reasoning, action = self._call_ai(device_id)
        if self._is_finish_action(action):
            self._complete_task(device_id)
            return True

        # Phase 2: Act - 执行动作
        result = self._execute_action(device_id, action)

        # Phase 3: Observe - 获取截图
        screenshot = self._get_screenshot(device_id)

        # 一轮完成，放回队列
        return False
```

## 9. 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         服务器                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              ReActScheduler 调度器                        │  │
│  │  ┌─────────────────────────────────────────────────────┐ │  │
│  │  │  任务队列 [d3, d4, d5, d1, d2]                     │ │  │
│  │  │  线程池: [线程A] [线程B] [线程C] [线程D]            │ │  │
│  │  └─────────────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                  │
│         ┌────────────────────┼────────────────────┐          │
│         ▼                    ▼                    ▼          │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐   │
│  │ DeviceTask1 │      │ DeviceTask2 │      │ DeviceTask3 │   │
│  │  device_id  │      │  device_id  │      │  device_id  │   │
│  │  task_id    │      │  task_id    │      │  task_id    │   │
│  │  context    │      │  context    │      │  context    │   │
│  │  react_recs │      │  react_recs │      │  react_recs │   │
│  │  phase      │      │  phase      │      │  phase      │   │
│  │  status     │      │  status     │      │  status     │   │
│  └─────────────┘      └─────────────┘      └─────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ WebSocket
                     ┌─────────────────────┐
                     │   Web 前端 (浏览器)  │
                     │   实时接收进度推送   │
                     └─────────────────────┘
```

## 10. 执行流程

```
1. 服务器启动线程池

2. 客户端通过WebSocket发送任务指令

3. 服务器创建DeviceTask，放入队列尾部
   队列: [device1, device2, device3, ...]

4. 线程池取任务执行
   线程A: device1 执行 Reason + Act + Observe
   线程B: device2 执行 Reason + Act + Observe
   线程C: 等待
   线程D: 等待

5. 一轮完成后放回队列尾部
   device1完成一轮 ──→ 队列: [device3, device4, ..., device1]
   device2完成一轮 ──→ 队列: [device5, device6, ..., device2]

6. 继续轮转直到所有任务完成

7. 通过WebSocket推送进度给前端
```

## 11. 关键设计点

1. **线程独立** - 每条线程独立工作，各负责一个device
2. **以ReAct为单位** - 每轮完成就放回队列，不独占线程
3. **公平轮转** - 先来先服务，但每次只服务一轮
4. **队列安全** - 使用锁保证队列操作的线程安全
5. **上下文可恢复** - 任务上下文独立管理，支持截断
