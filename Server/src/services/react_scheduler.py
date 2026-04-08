"""
ReAct 线程池调度器
基于设计文档: docs/MAIN.md

完整 ReAct 流程实现:
1. AI 调用重试（最多 3 次，10s 超时）
2. ActionParse 校验和自重构（最多 3 次）
3. 设备状态检查
4. ACK 重试（3 次，15s 间隔）
5. 完整的 WAIT_FOR_PUSH / WAIT_OBSERVATION / FINISHED 状态机
6. 类型安全的回调接口
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
from typing import Optional, Callable, Any, TYPE_CHECKING

from openai import OpenAI

from src.config import settings

# 导入新增的类型和回调
from src.services.react_types import SessionStatus, ReActErrorType
from src.services.react_callbacks import ReActCallback, ReActStepEvent, ReActTaskEvent


# ==================== Token 计数工具 ====================

def _count_tokens(text: str) -> int:
    """
    估算文本的 token 数量。
    优先尝试 tiktoken（精确），否则用字符数 / 4 估算（中英混合文本的粗略估计）。
    """
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model("gpt-4o")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


def _count_message_tokens(msg: dict) -> int:
    """估算单条消息的 token 数量（含 role + content）。"""
    content = msg.get("content", "")
    role = msg.get("role", "")
    if isinstance(content, list):
        # 多模态消息：text 部分估算，image_url 占少量 token
        text_parts = [part.get("text", "") for part in content if part.get("type") == "text"]
        text = "\n".join(text_parts)
        image_count = sum(1 for part in content if part.get("type") == "image_url")
        return _count_tokens(f"{role}\n{text}") + image_count * 85
    return _count_tokens(f"{role}\n{content}")


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


# ==================== 异常类 ====================

class ReActException(Exception):
    """ReAct 基础异常"""
    def __init__(self, error_type: ReActErrorType, message: str):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


class RemoteAPIException(ReActException):
    """远程 API 调用异常"""
    def __init__(self, error_type: ReActErrorType, message: str):
        super().__init__(error_type, message)


class ActionParseException(ReActException):
    """Action 解析异常"""
    def __init__(self, error_type: ReActErrorType, message: str):
        super().__init__(error_type, message)


class DeviceStatusException(ReActException):
    """设备状态异常"""
    def __init__(self, error_type: ReActErrorType, message: str):
        super().__init__(error_type, message)


class DispatchException(ReActException):
    """下发任务异常"""
    def __init__(self, error_type: ReActErrorType, message: str):
        super().__init__(error_type, message)


class ObserveException(ReActException):
    """观察结果异常"""
    def __init__(self, error_type: ReActErrorType, message: str):
        super().__init__(error_type, message)


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

    def truncate(self, max_tokens: int = 20000):
        """
        按 token 总数截断上下文（不含 system_prompt）。
        从最旧的消息开始丢弃，直到 assistant 消息总 token <= max_tokens。
        至少保留最近 1 条消息。
        """
        if not self.messages:
            return

        # 先估算 system_prompt 的 token 数（不截断，但计入上限）
        system_tokens = _count_tokens(self.system_prompt)

        def total_tokens(msgs: list[dict]) -> int:
            return system_tokens + sum(_count_message_tokens(m) for m in msgs)

        original_count = len(self.messages)
        # 从最旧的消息开始丢弃（第0条是历史遗留，优先丢弃）
        while len(self.messages) > 1 and total_tokens(self.messages) > max_tokens:
            self.messages.pop(0)

        if original_count != len(self.messages):
            scheduler_logger.debug(
                f"[CONTEXT] Truncated from {original_count} to {len(self.messages)} messages, "
                f"total_tokens≈{total_tokens(self.messages)}, max={max_tokens}"
            )

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

    # 会话状态 (新增)
    session_status: SessionStatus = SessionStatus.WAIT_FOR_PUSH

    # 回调列表 (新增)
    callbacks: list = field(default_factory=list)

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

    # 反思提示词（action解析失败时注入给下一轮AI）
    reflection_prompt: str = ""

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
4. 如果屏幕上的明确文本已经表明用户要求的目标已完成，必须立即输出 do(action="finish", message="任务已完成")
5. 对于设置类任务，如果界面明确显示目标值已经等于用户要求的值，则任务已经完成，不要继续点击、滑动或进入其他页面
6. 只有在屏幕上没有出现明确完成证据时，才继续导航查找相关设置

用户指令: {self.instruction}"""

    def initialize(self):
        """初始化任务"""
        self.context = DeviceTaskContext(system_prompt=self.get_system_prompt())
        self.react_records = []
        self.current_step = 0
        self.phase = TaskPhase.REASON
        self.status = TaskStatus.PENDING
        self.session_status = SessionStatus.WAIT_FOR_PUSH
        self.last_active_at = time.time()

    async def execute_reason(self) -> tuple[str, dict, str]:
        """
        执行 Reason 阶段 - 调用AI模型获取思考和动作
        Returns: (reasoning, action_dict, raw_model_output)
        """
        print(f"[DEBUG] execute_reason START for {self.device_id}, {self.task_id}", flush=True)
        self.phase = TaskPhase.REASON
        self.last_active_at = time.time()
        await self._notify_status("reason_started", {})

        scheduler_logger.debug(f"[REASON] calling model: device={self.device_id}, task={self.task_id}, context_len={len(self.context.messages)}, has_screenshot={bool(self.react_records and self.react_records[-1].screenshot)}")

        try:
            # 构建用户消息文本，追加反思提示词（如有）
            user_text = f"用户指令: {self.instruction}\n请分析屏幕内容，决定下一步动作。"
            if self.reflection_prompt:
                user_text = f"{self.reflection_prompt}\n\n{user_text}"
                self.reflection_prompt = ""  # 使用后清空

            user_message = {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{self.react_records[-1].screenshot}"}} if self.react_records else {"type": "text", "text": "请分析屏幕内容"},
                    {"type": "text", "text": user_text}
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

            return reasoning, action_dict, content

        except asyncio.TimeoutError:
            print(f"[DEBUG] execute_reason TIMEOUT for {self.device_id}, {self.task_id}", flush=True)
            scheduler_logger.warning(f"[REASON] model timeout: device={self.device_id}, task={self.task_id}, timeout={settings.PHONE_AGENT_TIMEOUT}")
            raise RemoteAPIException(
                ReActErrorType.REMOTE_API_TIMEOUT,
                f"AI call timeout after {settings.PHONE_AGENT_TIMEOUT}s"
            )
        except Exception as e:
            print(f"[ERROR] execute_reason EXCEPTION for {self.device_id}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            scheduler_logger.error(f"[REASON] model error: device={self.device_id}, task={self.task_id}, error={e}")
            raise RemoteAPIException(
                ReActErrorType.REMOTE_API_TIMEOUT,
                f"AI call error: {str(e)}"
            )

    async def execute_act(self, action: dict, reasoning: str, round_version: int) -> dict:
        """
        执行 Act 阶段 - 发送动作到客户端并等待结果
        Returns: ActionRouter result dict
        """
        self.phase = TaskPhase.ACT
        self.last_active_at = time.time()

        scheduler_logger.info(
            f"[ACT] executing: device={self.device_id}, task={self.task_id}, "
            f"step={self.current_step}, version={round_version}, action={action.get('action')}, params={action}"
        )

        # 检查是否是finish动作
        action_type = action.get("action", "").lower()
        if action_type in ["finish", "stop", "done"]:
            return {
                "success": True,
                "result": "_finish_",
                "version": round_version,
                "step_number": self.current_step,
            }

        # 使用ActionRouter发送动作到客户端并等待结果
        try:
            router = self.action_router
            if router:
                result = await router.execute_action(
                    task_id=self.task_id,
                    device_id=self.device_id,
                    action=action,
                    reasoning=reasoning,
                    step_number=self.current_step,
                    round_version=round_version,
                    ack_timeout_seconds=15.0,
                    observe_timeout_seconds=30.0,
                )

                scheduler_logger.info(
                    f"[ACT] action completed via ActionRouter: device={self.device_id}, task={self.task_id}, "
                    f"success={result.get('success', False)}, result_preview={str(result.get('result', ''))[:200]}"
                )

                # 检查结果中的错误类型
                error_type = result.get("error_type")
                if error_type == "ack_timeout":
                    raise DispatchException(
                        ReActErrorType.ACK_TIMEOUT,
                        "ACK timeout"
                    )
                elif error_type == "ack_rejected":
                    raise DispatchException(
                        ReActErrorType.ACK_REJECTED,
                        result.get("error", "Action rejected by client")
                    )
                elif error_type == "observe_timeout":
                    raise ObserveException(
                        ReActErrorType.OBSERVE_TIMEOUT,
                        "Observation timeout"
                    )
                elif error_type == "observe_error":
                    raise ObserveException(
                        ReActErrorType.OBSERVE_ERROR,
                        result.get("error", "Observation error")
                    )

                # 如果成功获取了截图，更新到react_records
                if result.get("screenshot"):
                    if self.react_records:
                        self.react_records[-1].screenshot = result.get("screenshot")

                return result
            else:
                scheduler_logger.warning("[ACT] ActionRouter not available, using legacy executor")
                if self.action_executor:
                    legacy_result = await asyncio.to_thread(self.action_executor, self.device_id, action)
                    return {
                        "success": True,
                        "result": legacy_result.get("message", str(legacy_result)),
                        "version": round_version,
                        "step_number": self.current_step,
                    }
                return {
                    "success": False,
                    "result": "",
                    "error": "No executor configured",
                    "error_type": "send_failed",
                    "version": round_version,
                    "step_number": self.current_step,
                }
        except DispatchException:
            raise
        except ObserveException:
            raise
        except asyncio.TimeoutError:
            scheduler_logger.error(f"[ACT] timeout: device={self.device_id}, task={self.task_id}, timeout=30.0")
            raise ObserveException(
                ReActErrorType.OBSERVE_TIMEOUT,
                "ACT execution timeout"
            )
        except Exception as e:
            scheduler_logger.error(f"[ACT] error: device={self.device_id}, task={self.task_id}, error={e}")
            raise DispatchException(
                ReActErrorType.OBSERVE_ERROR,
                f"Action error: {str(e)}"
            )

    def set_observe(self, screenshot: str, observation: str = ""):
        """设置观察结果"""
        if self.react_records:
            self.react_records[-1].screenshot = screenshot
            self.react_records[-1].observation = observation

        self.phase = TaskPhase.OBSERVE
        self.last_active_at = time.time()
        self.session_status = SessionStatus.WAIT_OBSERVATION

        # Save screenshot and update react record in file storage
        try:
            from src.services.file_storage import file_storage
            if screenshot and self.react_records:
                step = self.react_records[-1].step_number
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_path = file_storage.save_screenshot(self.device_id, step, ts, screenshot)
                file_storage.append_react_record(self.device_id, {
                    "step_number": step,
                    "screenshot": screenshot_path,
                    "observation": observation,
                    "phase": "observe",
                })
        except Exception as e:
            scheduler_logger.warning(f"[OBSERVE] Failed to save screenshot: {e}")

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

        # Save react record to file storage
        try:
            from src.services.file_storage import file_storage
            file_storage.append_react_record(self.device_id, {
                "step_number": record.step_number,
                "reasoning": reasoning,
                "action": action,
                "phase": "reason",
            })
        except Exception as e:
            scheduler_logger.warning(f"[REASON] Failed to save react record: {e}")

    def complete_act(self, result: str):
        """完成Act阶段"""
        if self.react_records:
            self.react_records[-1].action_result = result
            if result and not self.react_records[-1].observation:
                self.react_records[-1].observation = result
        self.phase = TaskPhase.OBSERVE
        self.last_active_at = time.time()
        self.session_status = SessionStatus.WAIT_OBSERVATION

        scheduler_logger.debug(f"[ACT] act completed: device={self.device_id}, task={self.task_id}, result={result[:100] if result else ''}")

        # Update react record in file storage with action result
        try:
            from src.services.file_storage import file_storage
            if self.react_records:
                file_storage.append_react_record(self.device_id, {
                    "step_number": self.react_records[-1].step_number,
                    "action_result": result,
                    "observation": self.react_records[-1].observation,
                    "phase": "act",
                })
        except Exception as e:
            scheduler_logger.warning(f"[ACT] Failed to update react record: {e}")

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
    基于设计文档: docs/MAIN.md

    核心逻辑:
    1. 线程池执行各device的一轮ReAct
    2. 每轮完成后放回队列尾部，公平轮转
    3. 通过WebSocket推送进度给客户端

    完整 ReAct 流程:
    1. AI 调用重试（最多 3 次，10s 超时）
    2. ActionParse 校验和自重构（最多 3 次）
    3. 设备状态检查
    4. ACK 重试（3 次，15s 间隔）
    5. 完整的 WAIT_FOR_PUSH / WAIT_OBSERVATION / FINISHED 状态机
    """

    # 重试配置
    MAX_AI_RETRIES = 3
    AI_TIMEOUT = 10.0
    MAX_ACK_RETRIES = 3
    ACK_RETRY_INTERVAL = 15.0

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
        step_callback: Optional[Callable] = None,
        callbacks: Optional[list] = None,
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
            step_callback=step_callback,
            callbacks=callbacks or [],
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

        # Save initial context to file storage
        try:
            from src.services.file_storage import file_storage
            context_data = {
                "system_prompt": task.context.system_prompt,
                "messages": task.context.messages,
            }
            file_storage.save_context(device_id, context_data)
            # Initialize chat history with user instruction
            file_storage.save_chat_history(device_id, [{
                "id": f"msg_{task_id}_user",
                "role": "user",
                "content": instruction,
                "created_at": datetime.now().isoformat(),
            }])
        except Exception as e:
            scheduler_logger.warning(f"[SUBMIT] Failed to save initial context: {e}")

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
                # 截断过长的上下文（按 token 总数不超过 20000）
                task.context.truncate(max_tokens=20000)
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

    # ==================== 重试方法 ====================

    async def _reason_with_retry(self, task: DeviceTask) -> tuple[str, dict, str]:
        """
        AI 推理重试 - 最多 3 次，每次 10s 超时
        Raises:
            RemoteAPIException: 当所有重试都失败时
        """
        for attempt in range(self.MAX_AI_RETRIES):
            try:
                scheduler_logger.info(
                    f"[REASON_RETRY] attempt {attempt + 1}/{self.MAX_AI_RETRIES} "
                    f"for device={task.device_id}, task={task.task_id}"
                )
                return await asyncio.wait_for(
                    task.execute_reason(),
                    timeout=self.AI_TIMEOUT
                )
            except asyncio.TimeoutError:
                scheduler_logger.warning(
                    f"[REASON_RETRY] timeout on attempt {attempt + 1}/{self.MAX_AI_RETRIES} "
                    f"for device={task.device_id}, task={task.task_id}"
                )
                if attempt == self.MAX_AI_RETRIES - 1:
                    raise RemoteAPIException(
                        ReActErrorType.REMOTE_API_RETRIES_EXCEEDED,
                        f"AI call failed after {self.MAX_AI_RETRIES} retries"
                    )
            except RemoteAPIException:
                raise
            except Exception as e:
                scheduler_logger.error(
                    f"[REASON_RETRY] error on attempt {attempt + 1}/{self.MAX_AI_RETRIES} "
                    f"for device={task.device_id}: {e}"
                )
                if attempt == self.MAX_AI_RETRIES - 1:
                    raise RemoteAPIException(
                        ReActErrorType.REMOTE_API_RETRIES_EXCEEDED,
                        f"AI call failed after {self.MAX_AI_RETRIES} retries: {str(e)}"
                    )

    async def _action_parse_with_retry(self, task: DeviceTask, reasoning: str, raw_output: str):
        """
        Action 解析重试 - 最多 3 次，包含自重构
        Returns: ActionParseResult
        """
        from src.services.action_parser import ActionParser, ActionParseResult

        action_parser = ActionParser(task.model_client)
        return await action_parser.parse_and_validate(
            reasoning=reasoning,
            raw_model_output=raw_output,
            device_type="android",
            attempt=1,
        )

    async def _check_device_status(self, task: DeviceTask):
        """
        设备状态检查
        Raises:
            DeviceStatusException: 当设备状态非 OK 时
        """
        try:
            from src.services.device_status_manager import device_status_manager

            status = await device_status_manager.get_status(task.device_id)
            if status.value != "idle":
                raise DeviceStatusException(
                    ReActErrorType.DEVICE_STATUS_UNEXPECTED,
                    f"Device status is {status}, expected idle"
                )
        except DeviceStatusException:
            raise
        except Exception as e:
            scheduler_logger.warning(f"[DEVICE_STATUS] check failed for {task.device_id}: {e}")
            # 设备状态管理器可能不存在，忽略错误

    async def _dispatch_with_retry(self, task: DeviceTask, action: dict, reasoning: str, round_version: int) -> dict:
        """
        下发任务重试 - ActionRouter 内部已有重试逻辑
        Raises:
            DispatchException: 当 ACK 超时或被拒绝时
            ObserveException: 当观察结果超时或错误时
        """
        try:
            return await task.execute_act(action, reasoning, round_version)
        except (DispatchException, ObserveException):
            raise
        except Exception as e:
            raise DispatchException(
                ReActErrorType.OBSERVE_ERROR,
                f"Dispatch error: {str(e)}"
            )

    # ==================== 回调方法 ====================

    async def _emit_phase_start(self, device_id: str, task_id: str, phase: str, step: int):
        """发送阶段开始事件"""
        task = self._device_tasks.get(device_id)
        if task:
            for cb in task.callbacks:
                try:
                    if hasattr(cb, 'on_phase_start'):
                        await cb.on_phase_start(device_id, task_id, phase, step)
                except Exception as e:
                    scheduler_logger.warning(f"[CALLBACK] on_phase_start error: {e}")

        if self._ws_hub:
            await self._ws_hub.broadcast_agent_phase_start(
                device_id=device_id,
                task_id=task_id,
                phase=phase,
                step_number=step,
            )

    async def _emit_step(self, task: DeviceTask, reasoning: str, action: dict, result: str, screenshot: str = ""):
        """发送步骤完成事件"""
        event = ReActStepEvent(
            device_id=task.device_id,
            task_id=task.task_id,
            step_number=task.current_step,
            phase=task.phase.value,
            reasoning=reasoning,
            action=action,
            result=result,
            screenshot=screenshot or (task.react_records[-1].screenshot if task.react_records else None),
            success=True,
            error=None,
            error_type=None,
        )

        for cb in task.callbacks:
            try:
                if hasattr(cb, 'on_step'):
                    await cb.on_step(event)
            except Exception as e:
                scheduler_logger.warning(f"[CALLBACK] on_step error: {e}")

        if self._ws_hub:
            await self._ws_hub.broadcast_agent_step(
                task_id=task.task_id,
                device_id=task.device_id,
                step=event.__dict__,
                step_type="agent_step",
            )

    async def _emit_complete(self, task: DeviceTask, final_reasoning: str):
        """发送任务完成事件"""
        task.status = TaskStatus.COMPLETED
        task.session_status = SessionStatus.FINISHED

        event = ReActTaskEvent(
            device_id=task.device_id,
            task_id=task.task_id,
            status="completed",
            message="Task completed",
            final_reasoning=final_reasoning,
            error_type=None,
        )

        for cb in task.callbacks:
            try:
                if hasattr(cb, 'on_task_complete'):
                    await cb.on_task_complete(event)
            except Exception as e:
                scheduler_logger.warning(f"[CALLBACK] on_task_complete error: {e}")

        if self._ws_hub:
            await self._ws_hub.broadcast_agent_status(
                device_id=task.device_id,
                session_id=task.task_id,
                status="completed",
                message="Task completed",
                data={
                    "task_id": task.task_id,
                    "final_reasoning": final_reasoning,
                },
            )

        # 将设备状态重置为 idle
        from src.services.device_status_manager import device_status_manager
        await device_status_manager.set_idle(task.device_id)

        # 从调度器内存中移除任务
        self.remove_task(task.device_id)

    async def _emit_failed(self, task: DeviceTask, message: str, error_type: ReActErrorType, final_reasoning: str = ""):
        """发送任务失败事件"""
        task.status = TaskStatus.COMPLETED  # 标记为完成（结束）
        task.session_status = SessionStatus.FINISHED

        event = ReActTaskEvent(
            device_id=task.device_id,
            task_id=task.task_id,
            status="failed",
            message=message,
            final_reasoning=final_reasoning,
            error_type=error_type.value if error_type else None,
        )

        for cb in task.callbacks:
            try:
                if hasattr(cb, 'on_task_failed'):
                    await cb.on_task_failed(event)
            except Exception as e:
                scheduler_logger.warning(f"[CALLBACK] on_task_failed error: {e}")

        if self._ws_hub:
            await self._ws_hub.broadcast_agent_status(
                device_id=task.device_id,
                session_id=task.task_id,
                status="failed",
                message=message,
                data={
                    "task_id": task.task_id,
                    "error_type": error_type.value if error_type else None,
                    "final_reasoning": final_reasoning,
                },
            )

        # 将设备状态重置为 idle
        from src.services.device_status_manager import device_status_manager
        await device_status_manager.set_idle(task.device_id)

        # 从调度器内存中移除任务
        self.remove_task(task.device_id)

    # ==================== 主循环 ====================

    async def run_one_cycle(self, device_id: str) -> bool:
        """
        执行一个完整的 ReAct 循环
        Returns: True=任务完成/需切换, False=继续当前任务

        完整流程:
        1. REASON: AI 推理重试（最多 3 次，10s 超时）
        2. ActionParse: 校验和自重构（最多 3 次）
        3. 设备状态检查
        4. ACT: 下发任务（ACK 重试 3 次，15s 间隔）
        5. OBSERVE: 等待观察结果
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

        try:
            next_step_number = task.current_step + 1

            # === Phase 1: REASON ===
            await self._emit_phase_start(device_id, task.task_id, "reason", next_step_number)
            reasoning, action, raw_output = await self._reason_with_retry(task)
            scheduler_logger.info(
                f"[CYCLE] reason completed: device={device_id}, task={task.task_id}, "
                f"step={next_step_number}, action_guess={action.get('action')}, reasoning_preview={reasoning[:120] if reasoning else ''}"
            )

            # 检查是否完成
            if task._is_finish_action(action):
                scheduler_logger.info(
                    f"[CYCLE] finish action detected: device={device_id}, task={task.task_id}, step={next_step_number}"
                )
                await self._emit_complete(task, reasoning)
                return True

            # === ActionParse: 校验 action 是否有效 ===
            parse_result = await self._action_parse_with_retry(task, reasoning, raw_output)
            scheduler_logger.info(
                f"[CYCLE] parse completed: device={device_id}, task={task.task_id}, "
                f"step={next_step_number}, parse_success={parse_result.success}, parsed_action={parse_result.action}"
            )

            # 如果 action 解析失败且是 unknown 类型的错误，尝试重新推理
            action_type = action.get("action", "").lower()
            if action_type == "unknown" or not action.get("action"):
                scheduler_logger.warning(
                    f"[CYCLE] action parse failed: device={device_id}, task={task.task_id}, "
                    f"action={action.get('raw', '')[:50]}"
                )
                parse_error_msg = f"[动作解析失败] 无法解析上一步的输出内容: {action.get('raw', '')[:100]}...请重新分析屏幕并输出正确的动作。"
                task.set_observe(
                    screenshot=task.react_records[-1].screenshot if task.react_records else "",
                    observation=parse_error_msg
                )
                task.reflection_prompt = parse_error_msg  # 注入给下一轮 AI
                await self._emit_step(task, reasoning, action, parse_error_msg)
                self.requeue_task(device_id)
                return False

            if not parse_result.success:
                await self._emit_failed(
                    task,
                    parse_result.error or "Action parse failed",
                    parse_result.error_type or ReActErrorType.ACTION_PARSE_FAILED,
                    reasoning,
                )
                return True

            task.complete_reason(reasoning, parse_result.action)

            from src.services.device_status_manager import device_status_manager
            round_version = await device_status_manager.increment_version(device_id)

            # === Phase 2: ACT ===
            await self._emit_phase_start(device_id, task.task_id, "act", task.current_step)
            scheduler_logger.info(
                f"[CYCLE] dispatching action: device={device_id}, task={task.task_id}, "
                f"step={task.current_step}, version={round_version}, action={parse_result.action}"
            )
            dispatch_result = await self._dispatch_with_retry(task, parse_result.action, reasoning, round_version)
            result_text = dispatch_result.get("result", "")
            screenshot_b64 = dispatch_result.get("screenshot") or ""

            if result_text == "_finish_":
                await self._emit_complete(task, reasoning)
                return True

            # === Phase 4: OBSERVE ===
            task.complete_act(result_text)
            await self._emit_phase_start(device_id, task.task_id, "observe", task.current_step)

            # 广播步骤
            await self._emit_step(
                task,
                reasoning,
                parse_result.action,
                result_text,
                screenshot_b64,
            )

            # 检查最大步数
            if task.current_step >= task.max_steps:
                await self._emit_complete(task, "达到最大步数限制")
                return True

            # 放回队列继续下一轮
            task.session_status = SessionStatus.WAIT_FOR_PUSH
            scheduler_logger.info(
                f"[CYCLE] cycle completed: device={device_id}, task={task.task_id}, "
                f"completed_step={task.current_step}, version={round_version}, "
                f"queue_size={len(self._task_queue)}"
            )
            self.requeue_task(device_id)
            return False

        # ==================== 异常处理 ====================
        except RemoteAPIException as e:
            scheduler_logger.error(
                f"[CYCLE] RemoteAPIException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            await self._emit_failed(task, e.message, e.error_type)
            return True

        except ActionParseException as e:
            scheduler_logger.error(
                f"[CYCLE] ActionParseException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            await self._emit_failed(task, e.message, e.error_type)
            return True

        except DeviceStatusException as e:
            scheduler_logger.error(
                f"[CYCLE] DeviceStatusException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            await self._emit_failed(task, e.message, e.error_type)
            return True

        except DispatchException as e:
            scheduler_logger.error(
                f"[CYCLE] DispatchException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            await self._emit_failed(task, e.message, e.error_type)
            return True

        except ObserveException as e:
            scheduler_logger.error(
                f"[CYCLE] ObserveException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            await self._emit_failed(task, e.message, e.error_type)
            return True

        except Exception as e:
            scheduler_logger.error(
                f"[CYCLE] Unexpected exception: device={device_id}, task={task.task_id}, error={e}",
                exc_info=True,
            )
            await self._emit_failed(task, str(e), ReActErrorType.OBSERVE_ERROR)
            return True

    async def set_observe_result(
        self,
        device_id: str,
        screenshot: str,
        observation: str,
        *,
        step_number: Optional[int] = None,
        round_version: Optional[int] = None,
        screenshot_path: Optional[str] = None,
        success: bool = True,
        error: Optional[str] = None,
    ):
        """设置 Observe 结果，允许在 ACT 等待阶段提前写入当前 step。"""
        task = self._device_tasks.get(device_id)
        if not task or not task.is_active or not task.react_records:
            scheduler_logger.info(
                f"[OBSERVE] skip update: device={device_id}, has_task={bool(task)}, "
                f"active={task.is_active if task else False}, records={len(task.react_records) if task else 0}, "
                f"step={step_number}, version={round_version}"
            )
            return

        record = None
        if step_number is not None:
            for candidate in reversed(task.react_records):
                if candidate.step_number == step_number:
                    record = candidate
                    break
        if record is None:
            record = task.react_records[-1]

        if screenshot:
            record.screenshot = screenshot
        if observation:
            record.observation = observation
        record.success = success and not error
        task.last_active_at = time.time()

        scheduler_logger.info(
            f"[OBSERVE] stored result: device={device_id}, task={task.task_id}, "
            f"step={record.step_number}, version={round_version}, success={record.success}, "
            f"has_screenshot={bool(screenshot)}, phase={task.phase.value}"
        )

        try:
            from src.services.file_storage import file_storage
            file_storage.append_react_record(device_id, {
                "step_number": record.step_number,
                "screenshot": screenshot_path,
                "observation": observation,
                "success": record.success,
                "error": error,
                "phase": "observe",
                "version": round_version,
            })
        except Exception as e:
            scheduler_logger.warning(f"[OBSERVE] Failed to save observe record: {e}")

    async def interrupt_task(self, device_id: str):
        """中断任务"""
        task = self._device_tasks.get(device_id)
        if not task:
            return

        task.status = TaskStatus.INTERRUPTED
        task.session_status = SessionStatus.FINISHED

        # Cancel pending action
        try:
            from src.services.action_router import action_router

            if action_router:
                await action_router.cancel_action(task.task_id, device_id)
        except Exception as e:
            scheduler_logger.warning(f"[INTERRUPT] cancel failed: device={device_id}, task={task.task_id}, error={e}")

        # Clear context from file storage
        try:
            from src.services.file_storage import file_storage
            file_storage.clear_context(device_id)
        except Exception as e:
            scheduler_logger.warning(f"[INTERRUPT] Failed to clear context: {e}")

        # 发送中断事件
        event = ReActTaskEvent(
            device_id=task.device_id,
            task_id=task.task_id,
            status="interrupted",
            message="Task interrupted by user",
            final_reasoning=None,
            error_type=None,
        )
        for cb in task.callbacks:
            try:
                if hasattr(cb, 'on_task_failed'):
                    await cb.on_task_failed(event)
            except Exception as e:
                scheduler_logger.warning(f"[CALLBACK] on_task_failed error: {e}")

        self.remove_task(device_id)

        # 将设备状态重置为 idle
        from src.services.device_status_manager import device_status_manager
        await device_status_manager.set_idle(device_id)

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

        if task:
            task.status = TaskStatus.INTERRUPTED
            task.session_status = SessionStatus.FINISHED

        self.remove_task(device_id)

        # Note: Don't mark device as offline here - let HTTP device_status control the status
        # The device might be reconnecting via WebSocket. If it actually goes offline,
        # the offline checker will mark it as offline based on missed heartbeats.

        if self._ws_hub:
            await self._ws_hub.broadcast_agent_status(
                device_id=device_id,
                status="interrupted",
                message="设备连接断开，任务已中断",
                data={"task_id": active_task_id or "", "reason": "device_disconnected"},
            )

        scheduler_logger.info(
            f"[DISCONNECT] cleanup completed: device={device_id}, task={active_task_id}"
        )
        return bool(active_task_id)

    async def _broadcast_step(self, device_id: str, step: ReActRecord):
        """广播步骤完成（兼容旧接口）"""
        if self._ws_hub:
            await self._ws_hub.broadcast_agent_step(
                task_id="",
                device_id=device_id,
                step=step.__dict__,
                step_type="agent_step"
            )

    async def _broadcast_complete(self, device_id: str, message: str):
        """广播任务完成（兼容旧接口）"""
        if self._ws_hub:
            await self._ws_hub.broadcast_agent_status(
                device_id=device_id,
                status="completed",
                message=message
            )

        # 从调度器内存中移除任务
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
