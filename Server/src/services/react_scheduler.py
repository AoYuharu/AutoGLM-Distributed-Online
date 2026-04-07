"""
ReAct 线程池调度器
基于设计文档: react_scheduler_design.md
"""
import asyncio
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import Lock
from typing import Optional, Callable, Any

from sqlalchemy import select

from openai import OpenAI

from src.config import settings
from src.database import get_db_session
from src.models.models import Device, Task


# ==================== 日志配置 ====================

# 创建logger
scheduler_logger = logging.getLogger("react_scheduler")
scheduler_logger.setLevel(logging.DEBUG)

# 控制台handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)
console_formatter = logging.Formatter(
    '[%(asctime)s] [%(threadName)s] [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
console_handler.setFormatter(console_formatter)

# 文件handler - 写入日志文件
file_handler = logging.FileHandler('logs/react_scheduler.log', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    '[%(asctime)s] [%(threadName)s] [%(levelname)s] [%(funcName)s:%(lineno)d] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(file_formatter)

scheduler_logger.addHandler(console_handler)
scheduler_logger.addHandler(file_handler)


# ==================== 枚举定义 ====================

class TaskPhase(Enum):
    IDLE = "idle"
    REASON = "reason"
    ACT = "act"
    OBSERVE = "observe"


class TaskStatus(Enum):
    PENDING = "pending"           # 队列中等待
    RUNNING = "running"          # 正在执行
    WAITING_CONFIRMATION = "waiting_confirmation"  # 谨慎模式等待确认
    COMPLETED = "completed"       # 已完成
    INTERRUPTED = "interrupted"   # 被中断


# ==================== 数据类 ====================

@dataclass
class DeviceTaskContext:
    """设备任务上下文 - 可截断/恢复"""
    system_prompt: str = ""
    messages: list[dict] = field(default_factory=list)

    def add_message(self, role: str, content: Any):
        """添加消息"""
        if isinstance(content, dict):
            self.messages.append({"role": role, **content})
        else:
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


@dataclass
class ReActRecord:
    """单次 ReAct 执行记录"""
    step_number: int
    reasoning: str = ""
    action: dict = field(default_factory=dict)
    action_result: str = ""
    observation: str = ""
    screenshot: str = ""
    success: bool = True


# ==================== 单个设备任务类 ====================

@dataclass
class DeviceTask:
    """单个设备任务"""
    device_id: str
    task_id: str
    instruction: str
    mode: str = "normal"  # normal / cautious

    # 上下文
    context: Optional[DeviceTaskContext] = None

    # ReAct记录
    react_records: list[ReActRecord] = field(default_factory=list)

    # 状态
    phase: TaskPhase = TaskPhase.IDLE
    status: TaskStatus = TaskStatus.PENDING

    # 进度
    current_step: int = 0
    max_steps: int = 100

    # 超时配置
    observe_timeout: float = 60.0  # 等待screenshot的超时时间（秒）

    # 回调
    status_callback: Optional[Callable] = None
    step_callback: Optional[Callable] = None

    # 动作执行器
    action_executor: Optional[Callable] = None

    # Action Router (用于发送action到客户端)
    _action_router: Optional[Any] = None

    # AI客户端 (不使用field,直接在property中初始化)
    _model_client: Optional[Any] = None

    # 时间戳
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.context is None:
            self.context = DeviceTaskContext()

    @property
    def model_client(self) -> OpenAI:
        if self._model_client is None:
            self._model_client = OpenAI(
                base_url=settings.PHONE_AGENT_BASE_URL,
                api_key=settings.PHONE_AGENT_API_KEY
            )
        return self._model_client

    @property
    def action_router(self):
        """懒加载获取ActionRouter实例"""
        if self._action_router is None:
            from src.services.action_router import action_router
            self._action_router = action_router
        return self._action_router

    @property
    def is_active(self) -> bool:
        """是否处于活跃状态"""
        return self.status in [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.WAITING_CONFIRMATION]

    @property
    def is_finished(self) -> bool:
        """是否已完成"""
        return self.status in [TaskStatus.COMPLETED, TaskStatus.INTERRUPTED]

    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        today = datetime.today()
        formatted_date = today.strftime("%Y-%m-%d, %A")

        return f"""当前日期: {formatted_date}
# Setup
你是一个专业的Android手机操作助手。在每一步中，你会收到手机屏幕截图和用户指令。

# 你的任务
你必须根据用户指令执行操作。用户指令会告诉你需要完成什么任务。

# 输出格式
严格按以下格式输出：

首先思考（必须）：
<response>
分析当前屏幕，识别要执行的操作
</response>

然后输出动作（必须）：
<answer>
do(action="操作类型", ...)
</answer>

# 可用的操作
1. do(action="Launch", app="应用名") - 启动应用
2. do(action="Tap", element=[x,y]) - 点击屏幕坐标
3. do(action="Swipe", start=[x1,y1], end=[x2,y2]) - 滑动
4. do(action="Type", text="文本") - 输入文本
5. do(action="Back") - 返回
6. do(action="Home") - 返回桌面
7. do(action="Wait", duration="1") - 等待
8. do(action="finish", message="完成信息") - 完成任务

# 重要规则
1. 打开应用必须用 Launch 动作
2. 坐标范围是 0-999
3. 当前屏幕截图会作为图片提供给你分析

用户指令: {self.instruction}"""

    def initialize(self):
        """初始化任务"""
        self.context = DeviceTaskContext(system_prompt=self.get_system_prompt())
        self.react_records = []
        self.current_step = 0
        self.phase = TaskPhase.REASON
        self.status = TaskStatus.PENDING
        self.last_active_at = time.time()

    async def execute_reason(self) -> tuple[str, dict]:
        """
        执行 Reason 阶段 - 调用AI模型获取思考和动作
        Returns: (reasoning, action_dict)
        """
        print(f"[DEBUG] execute_reason START for {self.device_id}, {self.task_id}", flush=True)
        self.phase = TaskPhase.REASON
        self.last_active_at = time.time()
        await self._notify_status("reason_started", {})

        scheduler_logger.debug(f"[REASON] calling model: device={self.device_id}, task={self.task_id}, context_len={len(self.context.messages)}, has_screenshot={bool(self.react_records and self.react_records[-1].screenshot)}")

        try:
            # 构建消息
            user_message = {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{self.react_records[-1].screenshot}"}} if self.react_records else {"type": "text", "text": "请分析屏幕内容"},
                    {"type": "text", "text": f"用户指令: {self.instruction}\n请分析屏幕内容，决定下一步动作。"}
                ]
            }

            # 调用模型（同步调用，在线程中执行）
            start_time = time.time()
            print(f"[DEBUG] execute_reason calling AI API for {self.device_id}, timeout={settings.PHONE_AGENT_TIMEOUT}s", flush=True)
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.model_client.chat.completions.create,
                    model=settings.PHONE_AGENT_MODEL,
                    messages=self.context.to_api_format() + [user_message],
                    max_tokens=2048,
                    temperature=0.1,
                ),
                timeout=settings.PHONE_AGENT_TIMEOUT
            )
            print(f"[DEBUG] execute_reason got AI response for {self.device_id}", flush=True)
            latency = time.time() - start_time

            content = response.choices[0].message.content

            # 添加到上下文
            self.context.add_message("assistant", content)

            # 解析思考和动作
            reasoning, action_text = self._parse_action(content)
            action_dict = self._parse_action_to_dict(action_text)

            scheduler_logger.info(f"[REASON] model response: device={self.device_id}, task={self.task_id}, latency={latency:.1f}s, content_len={len(content)}, preview={content[:100]}")

            return reasoning, action_dict

        except asyncio.TimeoutError:
            print(f"[DEBUG] execute_reason TIMEOUT for {self.device_id}, {self.task_id}", flush=True)
            scheduler_logger.warning(f"[REASON] model timeout: device={self.device_id}, task={self.task_id}, timeout={settings.PHONE_AGENT_TIMEOUT}")
            return "AI模型响应超时", {"action": "error", "message": "model_timeout"}
        except Exception as e:
            print(f"[ERROR] execute_reason EXCEPTION for {self.device_id}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            scheduler_logger.error(f"[REASON] model error: device={self.device_id}, task={self.task_id}, error={e}")
            return f"Error: {str(e)}", {"action": "error", "message": str(e)}

    async def execute_act(self, action: dict) -> str:
        """
        执行 Act 阶段 - 发送动作到客户端并等待结果
        Returns: action_result
        """
        self.phase = TaskPhase.ACT
        self.last_active_at = time.time()

        scheduler_logger.info(f"[ACT] executing: device={self.device_id}, task={self.task_id}, action={action.get('action')}, params={action}")

        # 检查是否是finish动作
        action_type = action.get("action", "").lower()
        if action_type in ["finish", "stop", "done"]:
            return "_finish_"

        # 使用ActionRouter发送动作到客户端并等待结果
        try:
            router = self.action_router
            if router:
                result = await router.execute_action(
                    task_id=self.task_id,
                    device_id=self.device_id,
                    action=action,
                    reasoning="",  # reasoning already shown in step
                    step_number=self.current_step,
                    timeout_seconds=30.0,
                )

                scheduler_logger.info(
                    f"[ACT] action completed via ActionRouter: device={self.device_id}, task={self.task_id}, "
                    f"success={result.get('success', False)}, result_preview={str(result.get('result', ''))[:200]}"
                )

                # 如果成功获取了截图，更新到react_records
                if result.get("screenshot"):
                    if self.react_records:
                        self.react_records[-1].screenshot = result.get("screenshot")

                return result.get("result", str(result))
            else:
                scheduler_logger.warning("[ACT] ActionRouter not available, using legacy executor")
                if self.action_executor:
                    result = await asyncio.to_thread(self.action_executor, self.device_id, action)
                    return result.get("message", str(result))
                return "No executor configured"
        except asyncio.TimeoutError:
            scheduler_logger.error(f"[ACT] timeout: device={self.device_id}, task={self.task_id}, timeout=30.0")
            return "_observe_timeout_"  # 观察超时，等待截图无响应
        except Exception as e:
            scheduler_logger.error(f"[ACT] error: device={self.device_id}, task={self.task_id}, error={e}")
            return f"Action error: {str(e)}"

    def set_observe(self, screenshot: str, observation: str = ""):
        """设置观察结果"""
        if self.react_records:
            self.react_records[-1].screenshot = screenshot
            self.react_records[-1].observation = observation

        self.phase = TaskPhase.OBSERVE
        self.last_active_at = time.time()

    def complete_reason(self, reasoning: str, action: dict):
        """完成Reason阶段，记录ReAct"""
        record = ReActRecord(
            step_number=len(self.react_records) + 1,
            reasoning=reasoning,
            action=action
        )
        self.react_records.append(record)
        self.current_step = len(self.react_records)
        self.phase = TaskPhase.ACT
        self.last_active_at = time.time()

        scheduler_logger.debug(
            f"[REASON] reason phase completed: device={self.device_id}, task={self.task_id}, "
            f"step_number={record.step_number}, action_type={action.get('action')}, "
            f"reasoning_preview={reasoning[:200] if reasoning else ''}"
        )

    def complete_act(self, result: str):
        """完成Act阶段"""
        if self.react_records:
            self.react_records[-1].action_result = result
        self.phase = TaskPhase.OBSERVE
        self.last_active_at = time.time()

        scheduler_logger.debug(f"[ACT] act completed: device={self.device_id}, task={self.task_id}, result={result[:100] if result else ''}")

    def _parse_action(self, content: str) -> tuple[str, str]:
        """解析模型响应，分离思考和动作"""
        if "<answer>" in content:
            parts = content.split("<answer>")
            thinking = parts[0].replace("<response>", "").replace("</response>", "").strip()
            if len(parts) > 1:
                action = parts[1].split("</answer>")[0].strip()
            else:
                action = content
            return thinking, action

        if 'do(action=' in content:
            idx = content.index('do(action=')
            thinking = content[:idx].strip()
            action = content[idx:]
            match = re.search(r'do\([^)]+\)', action)
            if match:
                action = match.group(0)
            return thinking, action

        if "```json" in content:
            parts = content.split("```json")
            thinking = parts[0].strip()
            if len(parts) > 1:
                action = parts[1].split("```")[0].strip()
            else:
                action = content
            return thinking, action

        for marker in ["动作:", "动作：", "Action:", " 动作 "]:
            if marker in content:
                idx = content.index(marker)
                thinking = content[:idx].strip()
                action = content[idx + len(marker):].strip()
                return thinking, action

        if "finish(" in content:
            idx = content.index("finish(")
            thinking = content[:idx].strip()
            action = content[idx:].strip()
            return thinking, action

        return "", content.strip()

    def _parse_action_to_dict(self, action_text: str) -> dict:
        """将动作文本解析为字典"""
        action_text = action_text.strip()

        if "do(action=" in action_text:
            try:
                match = re.search(r'do\(action\s*=\s*["\']([^"\']+)["\']', action_text)
                if match:
                    action_type = match.group(1)
                    params = {}

                    element_match = re.search(r'element\s*=\s*\[(\d+),\s*(\d+)\]', action_text)
                    if element_match:
                        params["x"] = int(element_match.group(1))
                        params["y"] = int(element_match.group(2))

                    start_match = re.search(r'start\s*=\s*\[(\d+),\s*(\d+)\]', action_text)
                    end_match = re.search(r'end\s*=\s*\[(\d+),\s*(\d+)\]', action_text)
                    if start_match:
                        params["x1"] = int(start_match.group(1))
                        params["y1"] = int(start_match.group(2))
                    if end_match:
                        params["x2"] = int(end_match.group(1))
                        params["y2"] = int(end_match.group(2))

                    text_match = re.search(r'text\s*=\s*["\']([^"\']+)["\']', action_text)
                    if text_match:
                        params["text"] = text_match.group(1)

                    app_match = re.search(r'app\s*=\s*["\']([^"\']+)["\']', action_text)
                    if app_match:
                        params["app"] = app_match.group(1)

                    duration_match = re.search(r'duration\s*=\s*["\']?(\d+)\s*(?:seconds?)?["\']?', action_text, re.I)
                    if duration_match:
                        params["duration"] = int(duration_match.group(1))

                    return {"action": action_type, **params}
            except Exception:
                pass

        if "{" in action_text:
            try:
                start = action_text.index("{")
                end = action_text.rindex("}") + 1
                return json.loads(action_text[start:end])
            except (json.JSONDecodeError, ValueError):
                pass

        action_lower = action_text.lower()
        if "finish" in action_lower:
            return {"action": "finish"}
        elif "launch" in action_lower:
            return {"action": "Launch"}
        elif "long press" in action_lower:
            action_type = "Long Press"
            elem_match = re.search(r'\[(\d+),\s*(\d+)\]', action_text)
            params = {}
            if elem_match:
                params["x"] = int(elem_match.group(1))
                params["y"] = int(elem_match.group(2))
            return {"action": action_type, **params}
        elif "double tap" in action_lower:
            action_type = "Double Tap"
            elem_match = re.search(r'\[(\d+),\s*(\d+)\]', action_text)
            params = {}
            if elem_match:
                params["x"] = int(elem_match.group(1))
                params["y"] = int(elem_match.group(2))
            return {"action": action_type, **params}
        elif "tap" in action_lower:
            action_type = "Tap"
            elem_match = re.search(r'\[(\d+),\s*(\d+)\]', action_text)
            params = {}
            if elem_match:
                params["x"] = int(elem_match.group(1))
                params["y"] = int(elem_match.group(2))
            return {"action": action_type, **params}
        elif "swipe" in action_lower:
            action_type = "Swipe"
            start_match = re.search(r'start\s*=\s*\[(\d+),\s*(\d+)\]', action_text)
            end_match = re.search(r'end\s*=\s*\[(\d+),\s*(\d+)\]', action_text)
            params = {}
            if start_match:
                params["x1"] = int(start_match.group(1))
                params["y1"] = int(start_match.group(2))
            if end_match:
                params["x2"] = int(end_match.group(1))
                params["y2"] = int(end_match.group(2))
            return {"action": action_type, **params}
        elif "type" in action_lower:
            action_type = "Type"
            text_match = re.search(r'text\s*=\s*["\']([^"\']+)["\']', action_text)
            params = {}
            if text_match:
                params["text"] = text_match.group(1)
            return {"action": action_type, **params}
        elif "back" in action_lower:
            return {"action": "Back"}
        elif "home" in action_lower:
            return {"action": "Home"}
        elif "wait" in action_lower:
            action_type = "Wait"
            dur_match = re.search(r'(\d+)', action_text)
            params = {}
            if dur_match:
                params["duration"] = int(dur_match.group(1))
            return {"action": action_type, **params}

        return {"action": "unknown", "raw": action_text}

    def _is_finish_action(self, action: dict) -> bool:
        """判断是否是结束动作"""
        action_type = action.get("action", "").lower()
        return action_type in ["finish", "stop", "done"]

    async def _notify_status(self, status: str, data: dict):
        """通知状态变化"""
        if self.status_callback:
            await self.status_callback(self.device_id, status, data)

    async def _notify_step(self, step: ReActRecord):
        """通知步骤变化"""
        if self.step_callback:
            await self.step_callback(self.device_id, step)


# ==================== ReAct 调度器 ====================

class ReActScheduler:
    """
    ReAct 线程池调度器
    基于设计文档: react_scheduler_design.md

    核心逻辑:
    1. 线程池执行各device的一轮ReAct
    2. 每轮完成后放回队列尾部，公平轮转
    3. 通过WebSocket推送进度给客户端
    """

    def __init__(
        self,
        core_threads: int = 4,
        max_threads: int = 8,
        reason_timeout: int = 30,
        observe_timeout: int = 10
    ):
        self.core_threads = core_threads
        self.max_threads = max_threads
        self.reason_timeout = reason_timeout
        self.observe_timeout = observe_timeout

        # 线程池
        self.executor = ThreadPoolExecutor(
            max_workers=max_threads,
            thread_name_prefix="react_worker_"
        )

        # 任务队列
        self._task_queue: list[str] = []
        self._queue_lock = Lock()

        # device任务映射
        self._device_tasks: dict[str, DeviceTask] = {}

        # 上次记录的队列状态，用于减少重复日志
        self._last_queue_state: Optional[str] = None

        # 运行中的任务（thread_id -> device_id）
        self._running_tasks: dict[int, str] = {}
        self._running_lock = Lock()

        # 待确认的操作
        self._waiting_confirmations: dict[str, dict] = {}

        # WebSocket hub (用于推送)
        self._ws_hub = None

        # 是否正在运行
        self._running = False

        scheduler_logger.info(f"ReActScheduler initialized: core_threads={core_threads}, max_threads={max_threads}")

    def set_ws_hub(self, hub):
        """设置WebSocket Hub用于推送"""
        self._ws_hub = hub

    def submit_task(
        self,
        device_id: str,
        task_id: str,
        instruction: str,
        mode: str = "normal",
        max_steps: int = 100,
        action_executor: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        step_callback: Optional[Callable] = None
    ) -> DeviceTask:
        """提交新任务到队列尾部"""
        scheduler_logger.info(f"[SUBMIT] device={device_id}, task={task_id}, instruction={instruction[:30]}...")

        task = DeviceTask(
            device_id=device_id,
            task_id=task_id,
            instruction=instruction,
            mode=mode,
            max_steps=max_steps,
            action_executor=action_executor,
            status_callback=status_callback,
            step_callback=step_callback
        )
        task.initialize()

        with self._queue_lock:
            # 如果device已有任务在队列中，更新它而不是重复添加
            if device_id in self._device_tasks:
                scheduler_logger.info(f"[SUBMIT] Updating existing task for device={device_id}")
                self._device_tasks[device_id] = task
            else:
                self._device_tasks[device_id] = task
                self._task_queue.append(device_id)
                # 清空队列状态记录，因为队列不再为空
                self._last_queue_state = None
                scheduler_logger.info(f"[SUBMIT] Added to queue, queue_size={len(self._task_queue)}")

        return task

    def get_next_task(self) -> Optional[DeviceTask]:
        """从队列头部取任务"""
        with self._queue_lock:
            while self._task_queue:
                device_id = self._task_queue.pop(0)
                task = self._device_tasks.get(device_id)
                if task and task.is_active:
                    state_str = f"Acquired device={device_id}, task={task.task_id}, status={task.status.value}, phase={task.phase.value}"
                    # 只有状态改变时才记录
                    if state_str != self._last_queue_state:
                        scheduler_logger.info(f"[GET_TASK] {state_str}")
                        self._last_queue_state = state_str
                    return task
                else:
                    state_str = f"Skipping device={device_id}, is_active={task.is_active if task else 'task_not_found'}"
                    if state_str != self._last_queue_state:
                        scheduler_logger.debug(f"[GET_TASK] {state_str}")
                        self._last_queue_state = state_str

            # 只在队列从有任务变成空时记录一次
            if self._last_queue_state != "Queue empty":
                scheduler_logger.info("[GET_TASK] Queue empty, no tasks available")
                self._last_queue_state = "Queue empty"
            return None

    def requeue_task(self, device_id: str):
        """任务完成一轮后放回队列尾部"""
        with self._queue_lock:
            task = self._device_tasks.get(device_id)
            if task and task.is_active:
                # 截断过长的上下文
                task.context.truncate(10)
                self._task_queue.append(device_id)
                # 清空队列状态记录，因为队列不再为空
                self._last_queue_state = None
                scheduler_logger.info(f"[REQUEUE] device={device_id} requeued to tail, queue_size={len(self._task_queue)}, next_phase={task.phase.value}")
            else:
                scheduler_logger.warning(f"[REQUEUE] device={device_id} not requeued, is_active={task.is_active if task else 'task_not_found'}")

    def remove_task(self, device_id: str):
        """移除任务"""
        with self._queue_lock:
            if device_id in self._device_tasks:
                del self._device_tasks[device_id]
            self._task_queue = [queued_device_id for queued_device_id in self._task_queue if queued_device_id != device_id]
            self._waiting_confirmations.pop(device_id, None)
            self._running_tasks.pop(device_id, None)

    def get_task(self, device_id: str) -> Optional[DeviceTask]:
        """获取任务"""
        return self._device_tasks.get(device_id)

    def get_all_tasks(self) -> dict[str, DeviceTask]:
        """获取所有任务"""
        return self._device_tasks.copy()

    async def run_one_cycle(self, device_id: str) -> bool:
        """
        执行一个ReAct循环
        Returns: True=任务完成/需切换, False=继续当前任务
        """
        task = self._device_tasks.get(device_id)
        if not task or not task.is_active:
            scheduler_logger.warning(f"[CYCLE] device={device_id} task not found or inactive")
            return True

        # 进入循环时记录设备状态
        scheduler_logger.info(
            f"[CYCLE] cycle starting: device={device_id}, task={task.task_id}, "
            f"current_step={task.current_step + 1}, phase={task.phase.value}, "
            f"queue_size={len(self._task_queue)}, total_records={len(task.react_records)}"
        )

        # === Phase 1: REASON ===
        scheduler_logger.debug(f"[CYCLE] device={device_id} ENTER REASON phase")
        reasoning, action = await task.execute_reason()

        # REASON 阶段完成记录
        scheduler_logger.info(
            f"[REASON] reason phase completed: device={device_id}, task={task.task_id}, "
            f"action_type={action.get('action')}, reasoning_preview={reasoning[:300] if reasoning else ''}"
        )

        # 检查是否完成
        if task._is_finish_action(action):
            task.status = TaskStatus.COMPLETED
            scheduler_logger.info(f"[CYCLE] finished (finish action): device={device_id}, task={task.task_id}, steps={task.current_step}, records={len(task.react_records)}")
            await self._broadcast_complete(device_id, reasoning)
            return True

        # 记录ReAct
        task.complete_reason(reasoning, action)

        # === 容错检查: action 是否有效 ===
        action_type = action.get("action", "").lower()
        if action_type == "unknown" or not action.get("action"):
            # 动作无法解析，将错误信息作为观察结果反馈给agent，重新reason
            scheduler_logger.warning(f"[CYCLE] action parse failed: device={device_id}, task={task.task_id}, action={action.get('raw', '')[:50]}")

            # 将解析失败的信息作为观察添加到上下文，让agent重新思考
            parse_error_msg = f"[动作解析失败] 无法解析上一步的输出内容: {action.get('raw', '')[:100]}...请重新分析屏幕并输出正确的动作。"
            task.set_observe(
                screenshot=task.react_records[-1].screenshot if task.react_records else "",
                observation=parse_error_msg
            )

            # 广播错误步骤
            if task.react_records:
                await self._broadcast_step(device_id, task.react_records[-1])

            # 直接放回队列，重新reason（不执行act）
            self.requeue_task(device_id)
            return False

        # === Phase 2: ACT ===
        scheduler_logger.debug(f"[CYCLE] device={device_id} ENTER ACT phase, action={action}")
        result = await task.execute_act(action)

        # ACT 阶段完成记录
        scheduler_logger.info(f"[ACT] completed: device={device_id}, task={task.task_id}, action={action.get('action')}, result={result[:100] if result else ''}")

        if result == "_finish_":
            task.status = TaskStatus.COMPLETED
            scheduler_logger.info(f"[CYCLE] finished (_finish_): device={device_id}, task={task.task_id}, steps={task.current_step}, records={len(task.react_records)}")
            await self._broadcast_complete(device_id, reasoning)
            return True

        # 处理观察超时
        if result == "_observe_timeout_":
            await self._handle_observe_timeout(device_id, task)
            return True

        task.complete_act(result)

        # 检查是否达到最大步数
        if task.current_step >= task.max_steps:
            task.status = TaskStatus.COMPLETED
            scheduler_logger.info(f"[CYCLE] finished (max_steps): device={device_id}, task={task.task_id}, steps={task.current_step}/{task.max_steps}, records={len(task.react_records)}")
            await self._broadcast_complete(device_id, "达到最大步数限制")
            return True

        # === Phase 3: OBSERVE (获取截图) ===
        # 由于截图由Client返回，这里暂时标记等待
        # TODO: 需要通过WebSocket向Client请求截图
        scheduler_logger.debug(f"[CYCLE] device={device_id} ENTER OBSERVE phase (placeholder)")
        task.set_observe("", "等待Client返回截图...")

        # 广播进度
        if task.react_records:
            await self._broadcast_step(device_id, task.react_records[-1])

        # 一轮完成，放回队列
        scheduler_logger.info(
            f"[CYCLE] cycle completed: device={device_id}, task={task.task_id}, "
            f"completed_step={task.current_step}, queue_size={len(self._task_queue)}"
        )
        self.requeue_task(device_id)
        return False

    async def set_observe_result(self, device_id: str, screenshot: str, observation: str):
        """设置Observe结果（由设备管理器调用）"""
        task = self._device_tasks.get(device_id)
        if task and task.phase == TaskPhase.OBSERVE:
            task.set_observe(screenshot, observation)

    async def interrupt_task(self, device_id: str):
        """中断任务"""
        task = self._device_tasks.get(device_id)
        if not task:
            return

        task.status = TaskStatus.INTERRUPTED

        # 更新数据库中的任务状态
        try:
            with get_db_session() as db:
                device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
                if device:
                    db_task = db.execute(
                        select(Task)
                        .where(Task.device_id == device.id)
                        .order_by(Task.created_at.desc())
                    ).scalars().first()
                    if db_task:
                        db_task.status = "interrupted"
                        db_task.finished_at = datetime.now()
                        scheduler_logger.info(f"[DB] Task {db_task.task_id} marked as interrupted in database")
        except Exception as e:
            scheduler_logger.error(f"[DB] Failed to update task status on interrupt: {e}")

        try:
            from src.services.action_router import action_router

            if action_router:
                await action_router.cancel_action(task.task_id, device_id)
        except Exception as e:
            scheduler_logger.warning(f"[INTERRUPT] cancel failed: device={device_id}, task={task.task_id}, error={e}")

        self.remove_task(device_id)

    async def cleanup_disconnected_device(self, device_id: str) -> bool:
        """设备断开连接后的权威清理。"""
        task = self._device_tasks.get(device_id)
        active_task_id = task.task_id if task and task.is_active else None

        scheduler_logger.info(f"[DISCONNECT] cleanup: device={device_id}, task={active_task_id}")

        try:
            from src.services.action_router import action_router

            if active_task_id and action_router:
                await action_router.cancel_action(active_task_id, device_id)
        except Exception as e:
            scheduler_logger.warning(f"[DISCONNECT] cancel failed: device={device_id}, task={active_task_id}, error={e}")

        db_task_updated = False
        with get_db_session() as db:
            device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
            if device:
                task_row = None
                if active_task_id:
                    task_row = db.execute(select(Task).where(Task.task_id == active_task_id)).scalar_one_or_none()
                if task_row is None and device.current_task_id:
                    task_row = db.execute(select(Task).where(Task.task_id == device.current_task_id)).scalar_one_or_none()

                if task_row and task_row.status in {"pending", "running"}:
                    now = datetime.utcnow()
                    task_row.status = "interrupted"
                    task_row.finished_at = now
                    task_row.error_message = "device_disconnected"
                    task_row.result = {
                        "success": False,
                        "error": "device_disconnected",
                        "message": "Device disconnected during task execution",
                    }
                    db_task_updated = True

                device.current_task_id = None
                device.status = "offline"

        if task:
            task.status = TaskStatus.INTERRUPTED
        self.remove_task(device_id)

        from src.services.device_status_manager import device_status_manager

        await device_status_manager.set_offline(device_id)

        if self._ws_hub:
            await self._ws_hub.broadcast_agent_status(
                device_id=device_id,
                status="interrupted",
                message="设备连接断开，任务已中断",
                data={"task_id": active_task_id or "", "reason": "device_disconnected"},
            )

        scheduler_logger.info(
            f"[DISCONNECT] cleanup completed: device={device_id}, task={active_task_id}, db_task_updated={db_task_updated}"
        )
        return bool(active_task_id or db_task_updated)

    async def _handle_observe_timeout(self, device_id: str, task: DeviceTask) -> None:
        """
        观察超时的处理策略:
        1. 检查设备连接状态
        2. 如果设备离线，标记任务为等待设备状态
        3. 如果设备在线，尝试重新请求截图
        """
        # 检查设备是否仍然连接
        if self._ws_hub and not self._ws_hub.is_device_connected(device_id):
            # 设备离线，任务进入等待状态
            task.status = TaskStatus.WAITING_CONFIRMATION  # 复用等待状态
            scheduler_logger.info(f"Task {task.task_id} waiting for device {device_id} to reconnect")
            # 不放回执行队列，等待设备重连
            if self._ws_hub:
                await self._ws_hub.broadcast_agent_status(
                    device_id=device_id,
                    session_id="",
                    status="waiting_device",
                    message=f"设备已离线，等待重连..."
                )
        else:
            # 设备在线但超时，可能是网络抖动，尝试重新获取截图
            scheduler_logger.warning(f"Observe timeout but device {device_id} still connected, will retry")
            task.set_observe("", "观察超时，正在重试...")
            # 重新放回队列尾部，下一轮重新尝试
            self.requeue_task(device_id)

    async def _broadcast_step(self, device_id: str, step: ReActRecord):
        """广播步骤完成"""
        if self._ws_hub:
            await self._ws_hub.broadcast_agent_step(
                device_id=device_id,
                step=step.__dict__,
                step_type="agent_step"
            )

    async def _broadcast_complete(self, device_id: str, message: str):
        """广播任务完成"""
        if self._ws_hub:
            await self._ws_hub.broadcast_agent_status(
                device_id=device_id,
                status="completed",
                message=message
            )

        # 更新数据库中的任务状态
        try:
            with get_db_session() as db:
                device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
                if device:
                    task = db.execute(
                        select(Task)
                        .where(Task.device_id == device.id)
                        .order_by(Task.created_at.desc())
                    ).scalars().first()
                    if task:
                        task.status = "completed"
                        task.finished_at = datetime.now()
                        scheduler_logger.info(f"[DB] Task {task.task_id} marked as completed in database")
        except Exception as e:
            scheduler_logger.error(f"[DB] Failed to update task status: {e}")

        # 从调度器内存中移除任务，确保 get_task() 返回 None
        self.remove_task(device_id)
        scheduler_logger.info(f"[SCHEDULER] Task removed from memory for device={device_id}")

    def start(self):
        """启动调度器（启动线程池）"""
        scheduler_logger.info(f"[START] Starting ReActScheduler with {self.core_threads} core threads")
        self._running = True
        for i in range(self.core_threads):
            self.executor.submit(self._worker_loop, i)
        scheduler_logger.info(f"[START] All {self.core_threads} worker threads submitted to executor")

    def stop(self):
        """停止调度器"""
        scheduler_logger.info("[STOP] Stopping ReActScheduler...")
        self._running = False
        self.executor.shutdown(wait=True)
        scheduler_logger.info("[STOP] ReActScheduler stopped")

    def _worker_loop(self, worker_id: int):
        """Worker线程主循环"""
        import threading
        thread_id = threading.current_thread().ident
        thread_name = f"react_worker_{worker_id}"

        scheduler_logger.info(f"[WORKER_{worker_id}] Started, thread_id={thread_id}")

        while self._running:
            # 获取下一个任务
            task = self.get_next_task()
            if not task:
                # 没有任务，短暂等待
                time.sleep(0.1)
                continue

            device_id = task.device_id

            with self._running_lock:
                self._running_tasks[thread_id] = device_id

            task.status = TaskStatus.RUNNING
            print(f"[WORKER_{worker_id}] SET STATUS TO RUNNING for {device_id}, {task.task_id}", flush=True)
            sys.stdout.flush()
            try:
                scheduler_logger.info(f"[WORKER_{worker_id}] task acquired: device={device_id}, task={task.task_id}, step={task.current_step}, phase={task.phase.value}, running={len(self._running_tasks)}")
            except Exception as e:
                print(f"[ERROR] Logger failed: {e}", flush=True)

            # 执行一轮ReAct
            try:
                print(f"[DEBUG] WORKER_{worker_id} creating event loop for {device_id}", flush=True)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                print(f"[DEBUG] WORKER_{worker_id} running cycle for {device_id}", flush=True)
                loop.run_until_complete(self.run_one_cycle(device_id))
                print(f"[DEBUG] WORKER_{worker_id} cycle finished for {device_id}", flush=True)
                loop.close()
                scheduler_logger.info(f"[WORKER_{worker_id}] cycle completed: device={device_id}, task={task.task_id}")
            except Exception as e:
                print(f"[ERROR] WORKER_{worker_id} Exception for {device_id}: {e}", flush=True)
                import traceback
                traceback.print_exc()
                scheduler_logger.error(f"[WORKER_{worker_id}] Error processing device={device_id}: {e}", exc_info=True)

            with self._running_lock:
                del self._running_tasks[thread_id]
                scheduler_logger.debug(f"[WORKER_{worker_id}] Removed from running_tasks, remaining: {list(self._running_tasks.values())}")


# 全局单例
scheduler = ReActScheduler()
