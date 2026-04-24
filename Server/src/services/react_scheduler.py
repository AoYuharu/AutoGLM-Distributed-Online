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
import base64
import binascii
import json
import logging
import os
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
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
    """估算单条消息的 token 数量（含 role + content）。

    截图 token 估算逻辑：
    - 视觉模型（ViT）对图像编码的 token 数取决于分辨率，而非文件大小
    - PNG 压缩不影响 token 数（模型解码后按像素处理）
    - 实测 1080x2400 截图：base64_len / 1000 ≈ 2900 tokens（与压缩率无关）
    - 公式推导：base64_len / 4 * 3 = 压缩PNG字节，PNG压缩率≈0.20
      → 原始像素 = base64_len / 4 * 3 / 0.20 / 3 ≈ base64_len / 2.67
      → 视觉token ≈ 原始像素 / 1000 ≈ base64_len / 2670 ≈ base64_len / 1000（整数）
    - 每张截图估算 base64_len / 1000，最少 3000 tokens（保守上限，覆盖绝大多数分辨率）
    """
    content = msg.get("content", "")
    role = msg.get("role", "")
    if isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if part.get("type") == "text"]
        text = "\n".join(text_parts)
        text_tokens = _count_tokens(f"{role}\n{text}")

        image_tokens = 0
        for part in content:
            if part.get("type") == "image_url":
                image_url = part.get("image_url", {})
                url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                if url.startswith("data:"):
                    b64 = url.split(",", 1)[1] if "," in url else url
                    # base64_len / 1000 作为截图 token 估算
                    # 实测 1080x2400 截图: ~2900 tokens（向上取整留余量）
                    image_tokens += max(int(len(b64) / 1000), 3000)
                else:
                    # 外部 URL 截图，固定估算 200 tokens
                    image_tokens += 200

        return text_tokens + image_tokens
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

    def truncate(self, max_tokens: int = 18000):
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
        original_total = total_tokens(self.messages)

        # 详细日志：每次调用都输出（INFO 级别）
        msg_breakdown = []
        for i, m in enumerate(self.messages):
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                img_count = sum(1 for p in content if p.get("type") == "image_url")
                text_len = sum(len(p.get("text", "")) for p in content if p.get("type") == "text")
                tokens = _count_message_tokens(m)
                has_img = "Y" if img_count > 0 else "N"
                msg_breakdown.append(f"[{i}]{role}(img:{has_img},text:{text_len}chars,{tokens}tokens)")
            else:
                tokens = _count_message_tokens(m)
                msg_breakdown.append(f"[{i}]{role}({len(str(content))}chars,{tokens}tokens)")

        # 从最旧的消息开始丢弃（第0条是历史遗留，优先丢弃）
        while len(self.messages) > 1 and total_tokens(self.messages) > max_tokens:
            self.messages.pop(0)

        final_count = len(self.messages)
        final_total = total_tokens(self.messages)
        kept = final_count
        removed = original_count - final_count

        if removed > 0:
            # 发生截断
            scheduler_logger.info(
                f"[CONTEXT TRUNCATE] step={getattr(self, 'current_step', '?')}, "
                f"REMOVED {removed} msg(s), kept {kept}/{original_count} msgs, "
                f"tokens: {original_total} -> {final_total} (max={max_tokens}), "
                f"system={system_tokens}, "
                f"msgs=[{', '.join(msg_breakdown[:3])}...{f' (+{kept-3} more)' if kept > 3 else ''}]"
            )
        else:
            # 未触发截断，但仍记录当前状态
            scheduler_logger.info(
                f"[CONTEXT CHECK] step={getattr(self, 'current_step', '?')}, "
                f"kept {kept}/{original_count} msgs, "
                f"total_tokens={final_total} (max={max_tokens}), "
                f"system={system_tokens}, "
                f"msgs=[{', '.join(msg_breakdown)}]"
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
    screenshot_path: Optional[str] = None
    before_action_screenshot: str = ""
    before_action_screenshot_path: Optional[str] = None
    screenshot_unchanged: Optional[bool] = None
    success: bool = True


def _normalize_base64_payload(image_data: str) -> str:
    """Normalize screenshot payload for exact decoded-byte comparison."""
    if not image_data:
        return ""
    payload = image_data.strip()
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    return "".join(payload.split())


def _compare_base64_images_exact(before_image: str, after_image: str) -> tuple[Optional[bool], dict[str, Any]]:
    """Compare two base64 screenshots by exact decoded bytes."""
    before_payload = _normalize_base64_payload(before_image)
    after_payload = _normalize_base64_payload(after_image)

    debug_info: dict[str, Any] = {
        "has_before": bool(before_payload),
        "has_after": bool(after_payload),
        "before_length": len(before_payload),
        "after_length": len(after_payload),
        "before_error": None,
        "after_error": None,
        "before_hash": None,
        "after_hash": None,
    }

    if not before_payload or not after_payload:
        return None, debug_info

    before_bytes = None
    after_bytes = None

    try:
        before_bytes = base64.b64decode(before_payload, validate=True)
        debug_info["before_hash"] = hash(before_bytes)
    except (ValueError, binascii.Error) as exc:
        debug_info["before_error"] = str(exc)

    try:
        after_bytes = base64.b64decode(after_payload, validate=True)
        debug_info["after_hash"] = hash(after_bytes)
    except (ValueError, binascii.Error) as exc:
        debug_info["after_error"] = str(exc)

    if before_bytes is None or after_bytes is None:
        return None, debug_info

    return before_bytes == after_bytes, debug_info


@dataclass
class ReasonInputScreenshots:
    latest_screenshot: str = ""
    before_action_screenshot: str = ""
    current_observe_screenshot: str = ""
    unchanged_warning: bool = False

    @property
    def has_comparison_pair(self) -> bool:
        return bool(self.before_action_screenshot and self.current_observe_screenshot)

    @property
    def image_count(self) -> int:
        if self.has_comparison_pair:
            return 2
        return 1 if self.latest_screenshot else 0

    def build_prompt_prefix(self) -> str:
        if self.has_comparison_pair:
            prompt = (
                "请结合提供的两张屏幕截图分析当前界面，第一张是上一步动作执行前截图，"
                "第二张是最新观察截图；请基于两图对照决定下一步动作。"
            )
            if self.unchanged_warning:
                prompt += (
                    "\n\n强提示：最新观察截图与上一步动作执行前截图完全一致，"
                    "应视为上一条操作未生效。不要机械重复同一动作，请重新判断页面状态和下一步策略。"
                )
            return prompt
        if self.latest_screenshot:
            return "请结合提供的屏幕截图分析当前界面，决定下一步动作。"
        return "当前没有可用截图，请不要假设屏幕内容；只根据已有观察文本和用户指令决定下一步动作。"

    def build_content_parts(self) -> list[dict[str, Any]]:
        content_parts: list[dict[str, Any]] = []
        if self.has_comparison_pair:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{self.before_action_screenshot}"},
            })
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{self.current_observe_screenshot}"},
            })
        elif self.latest_screenshot:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{self.latest_screenshot}"},
            })
        return content_parts

    def estimate_image_tokens(self) -> int:
        if self.has_comparison_pair:
            return (
                max(int(len(self.before_action_screenshot) / 1000), 3000)
                + max(int(len(self.current_observe_screenshot) / 1000), 3000)
            )
        if self.latest_screenshot:
            return max(int(len(self.latest_screenshot) / 1000), 3000)
        return 0

    @classmethod
    def from_task(cls, task: "DeviceTask") -> "ReasonInputScreenshots":
        latest_screenshot = task.get_latest_screenshot()
        if task.react_records:
            latest_record = task.react_records[-1]
            if latest_record.before_action_screenshot and latest_record.screenshot:
                return cls(
                    latest_screenshot=latest_record.screenshot,
                    before_action_screenshot=latest_record.before_action_screenshot,
                    current_observe_screenshot=latest_record.screenshot,
                    unchanged_warning=latest_record.screenshot_unchanged is True,
                )
        return cls(latest_screenshot=latest_screenshot)


@dataclass
class ObserveErrorDecisionState:
    """Observe/action 执行错误超限后的用户决策状态"""
    task_id: str
    device_id: str
    message: str
    consecutive_count: int
    max_retries: int
    step_number: int
    error_type: str = ReActErrorType.OBSERVE_ERROR.value
    stage: str = "observe_error"
    message_id: str = field(default_factory=lambda: f"observe_error_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_payload(self) -> dict:
        return asdict(self)


# ==================== 单个设备任务类 ====================

@dataclass
class DeviceTask:
    """单个设备任务"""
    device_id: str
    task_id: str
    instruction: str
    mode: str = "normal"  # normal / cautious

    # 会话与运行身份
    # session_id: 持久会话ID，等于 task_id（兼容别名），跨多次 run 复用
    # run_id: 每次自动运行新建，用于 ACK/observe/interrupt/confirm 精确归属
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    session_started_at: Optional[float] = None  # Unix 时间戳
    run_started_at: Optional[float] = None  # Unix 时间戳

    # 上下文
    context: Optional[DeviceTaskContext] = None

    # ReAct记录
    react_records: list[ReActRecord] = field(default_factory=list)

    # 首轮 bootstrap 观察（独立于正常 react_records）
    initial_screenshot: str = ""
    initial_observation: str = ""
    initial_screenshot_path: Optional[str] = None
    initial_observe_success: bool = False

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
    observe_timeout: float = settings.REACT_OBSERVE_TIMEOUT  # 等待screenshot的超时时间（秒）

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

    # observe/action 执行失败恢复控制
    max_observe_error_retries: int = settings.REACT_MAX_OBSERVE_ERROR_RETRIES
    consecutive_observe_error_count: int = 0
    pending_observe_error_decision: Optional[ObserveErrorDecisionState] = None

    # 时间戳
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.context is None:
            self.context = DeviceTaskContext()

    @property
    def model_client(self) -> OpenAI:
        if self._model_client is None:
            import httpx
            proxies = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            if not proxies:
                # Try to read from Windows registry proxy setting
                try:
                    import winreg
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
                    proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
                    if proxy_enable:
                        proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
                        proxies = f"http://{proxy_server}"
                except Exception:
                    pass
            # httpx.Client 必须设置 timeout，否则 httpx 默认 300s 超时会绕过 asyncio.wait_for
            http_timeout = httpx.Timeout(timeout=settings.PHONE_AGENT_TIMEOUT)
            if proxies:
                self._model_client = OpenAI(
                    base_url=settings.PHONE_AGENT_BASE_URL,
                    api_key=settings.PHONE_AGENT_API_KEY,
                    http_client=httpx.Client(proxy=proxies, timeout=http_timeout),
                )
            else:
                self._model_client = OpenAI(
                    base_url=settings.PHONE_AGENT_BASE_URL,
                    api_key=settings.PHONE_AGENT_API_KEY,
                    http_client=httpx.Client(trust_env=False, timeout=http_timeout),
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
7. 当上下文中出现 [ObserveError] 标签时，表示上一条 action 命令执行失败或未成功落地；你必须把它视为纠错反馈，结合最新截图重新判断，不要机械重复同一错误动作

用户指令: {self.instruction}"""

    def build_observe_error_reflection(self, error_message: str, advice: str = "") -> str:
        base = (
            f"[ObserveError] {error_message}\n"
            "上一条 action 命令执行失败或未成功落地。请结合最新截图重新判断，"
            "避免重复同一错误动作或同一错误目标。"
        )
        advice = (advice or "").strip()
        if advice:
            base = f"{base}\n[UserAdvice] {advice}"
        return base

    def clear_observe_error_decision(self):
        self.pending_observe_error_decision = None
        if self.status == TaskStatus.WAITING_CONFIRMATION and self.session_status == SessionStatus.WAIT_USER_DECISION:
            self.status = TaskStatus.PENDING
            self.session_status = SessionStatus.WAIT_FOR_PUSH

    def mark_observe_error_recovered(self, error_message: str, advice: str = ""):
        self.reflection_prompt = self.build_observe_error_reflection(error_message, advice)
        self.clear_observe_error_decision()
        self.phase = TaskPhase.REASON
        self.status = TaskStatus.PENDING
        self.session_status = SessionStatus.WAIT_FOR_PUSH
        self.last_active_at = time.time()

    def handle_observe_error(self, error_message: str) -> Optional[ObserveErrorDecisionState]:
        normalized_error = (error_message or "Observation error").strip()
        if self.react_records:
            self.react_records[-1].success = False
        self.consecutive_observe_error_count += 1
        self.last_active_at = time.time()

        if self.consecutive_observe_error_count <= self.max_observe_error_retries:
            self.mark_observe_error_recovered(normalized_error)
            return None

        decision = ObserveErrorDecisionState(
            task_id=self.task_id,
            device_id=self.device_id,
            message=normalized_error,
            consecutive_count=self.consecutive_observe_error_count,
            max_retries=self.max_observe_error_retries,
            step_number=self.current_step,
        )
        self.pending_observe_error_decision = decision
        self.phase = TaskPhase.OBSERVE
        self.status = TaskStatus.WAITING_CONFIRMATION
        self.session_status = SessionStatus.WAIT_USER_DECISION
        return decision

    def resolve_observe_error_decision(self, decision: str, advice: str = "") -> Optional[ObserveErrorDecisionState]:
        pending = self.pending_observe_error_decision
        if not pending:
            return None

        if decision == "continue":
            self.mark_observe_error_recovered(pending.message, advice)
        else:
            self.status = TaskStatus.INTERRUPTED
            self.session_status = SessionStatus.FINISHED
            self.pending_observe_error_decision = None
        return pending

    def get_observe_error_prompt_payload(self) -> Optional[dict]:
        if not self.pending_observe_error_decision:
            return None
        return self.pending_observe_error_decision.to_payload()

    def is_waiting_observe_error_decision(self) -> bool:
        return self.pending_observe_error_decision is not None

    def reset_observe_error_counter(self):
        self.consecutive_observe_error_count = 0
        self.pending_observe_error_decision = None

    def annotate_latest_record_observe_error(self, error_message: str):
        if not self.react_records:
            return
        record = self.react_records[-1]
        record.success = False
        if error_message:
            record.observation = error_message
            if not record.action_result:
                record.action_result = error_message

    def update_latest_observe(self, screenshot: str, observation: str = "", success: bool = True, screenshot_path: Optional[str] = None):
        if self.react_records:
            record = self.react_records[-1]
            if screenshot:
                record.screenshot = screenshot
            if screenshot_path:
                record.screenshot_path = screenshot_path
            if observation:
                record.observation = observation
            record.success = success
        self.phase = TaskPhase.OBSERVE
        self.last_active_at = time.time()
        self.session_status = SessionStatus.WAIT_OBSERVATION

        try:
            from src.services.file_storage import file_storage
            if self.react_records and (screenshot or observation):
                step = self.react_records[-1].step_number
                resolved_path = screenshot_path
                if screenshot and not resolved_path:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    resolved_path = file_storage.save_screenshot(self.device_id, step, ts, screenshot)
                file_storage.append_react_record(self.device_id, {
                    "step_number": step,
                    "screenshot": resolved_path,
                    "observation": observation,
                    "success": success,
                    "phase": "observe",
                })
        except Exception as e:
            scheduler_logger.warning(f"[OBSERVE] Failed to save screenshot: {e}")

    def get_latest_observation(self) -> str:
        if self.react_records and self.react_records[-1].observation:
            return self.react_records[-1].observation
        return self.initial_observation

    def get_latest_screenshot_path(self) -> Optional[str]:
        if self.react_records and self.react_records[-1].screenshot_path:
            return self.react_records[-1].screenshot_path
        return self.initial_screenshot_path

    def get_latest_error_reason(self) -> Optional[str]:
        pending = self.pending_observe_error_decision
        if pending:
            return pending.message
        if self.react_records and self.react_records[-1].success is False:
            return self.react_records[-1].observation or self.react_records[-1].action_result or None
        return None

    def to_observe_error_status_message(self) -> Optional[str]:
        pending = self.pending_observe_error_decision
        if not pending:
            return None
        return (
            f"Observe/action 连续失败 {pending.consecutive_count} 次，"
            f"已超过上限 {pending.max_retries}，等待用户决定是否继续。"
        )

    def to_observe_error_chat_message(self) -> Optional[dict]:
        pending = self.pending_observe_error_decision
        if not pending:
            return None
        content = self.to_observe_error_status_message() or pending.message
        return {
            "id": pending.message_id,
            "role": "agent",
            "content": content,
            "created_at": pending.created_at,
            "task_id": pending.task_id,
            "step_number": pending.step_number,
            "phase": "observe",
            "stage": "observe_error_user_decision",
            "progress_status_text": "observe_error_user_decision",
            "progress_message": content,
            "error": pending.message,
            "error_type": pending.error_type,
            "success": False,
            "data": pending.to_payload(),
        }

    def initialize(self):
        """初始化任务"""
        self.context = DeviceTaskContext(system_prompt=self.get_system_prompt())
        self.react_records = []
        self.initial_screenshot = ""
        self.initial_observation = ""
        self.initial_screenshot_path = None
        self.initial_observe_success = False
        self.current_step = 0
        self.phase = TaskPhase.REASON
        self.status = TaskStatus.PENDING
        self.session_status = SessionStatus.WAIT_FOR_PUSH
        self.last_active_at = time.time()

    def get_latest_screenshot(self) -> str:
        if self.react_records and self.react_records[-1].screenshot:
            return self.react_records[-1].screenshot
        return self.initial_screenshot

    def has_initial_screenshot(self) -> bool:
        return bool(self.initial_screenshot)

    async def execute_reason(self) -> tuple[str, dict, str]:
        """
        执行 Reason 阶段 - 调用AI模型获取思考和动作
        Returns: (reasoning, action_dict, raw_model_output)
        """
        print(f"[DEBUG] execute_reason START for {self.device_id}, {self.task_id}", flush=True)
        self.phase = TaskPhase.REASON
        self.last_active_at = time.time()
        await self._notify_status("reason_started", {})

        reason_screenshots = self.get_reason_input_screenshots()
        latest_screenshot = reason_screenshots.latest_screenshot
        user_message = self.build_reason_user_message()
        prompt_debug = self.get_reason_prompt_debug_info()

        # 完整 token 窗口日志
        system_tokens = _count_tokens(self.context.system_prompt)
        all_msgs = self.context.to_api_format()
        msg_breakdown = []
        total_text_tokens = 0
        total_image_tokens = 0
        for i, m in enumerate(all_msgs):
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                img_count = sum(1 for p in content if p.get("type") == "image_url")
                text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                text_len = sum(len(t) for t in text_parts)
                text_t = _count_tokens("\n".join(text_parts))
                # 只统计 text 部分，image 用 _count_message_tokens 里的估算
                img_t = _count_message_tokens(m) - text_t
                total_image_tokens += img_t
                total_text_tokens += text_t
                b64_len = 0
                for p in content:
                    if p.get("type") == "image_url":
                        url = p.get("image_url", {}).get("url", "") if isinstance(p.get("image_url"), dict) else ""
                        if url.startswith("data:"):
                            b = url.split(",", 1)[1] if "," in url else url
                            b64_len = len(b)
                msg_breakdown.append(
                    f"[{i}]{role}(img:{img_count},b64:{b64_len//1000}k,img_t:{img_t},text_t:{text_t})"
                )
            else:
                t_len = len(str(content))
                t_t = _count_tokens(str(content))
                total_text_tokens += t_t
                msg_breakdown.append(f"[{i}]{role}({t_len}chars,{t_t}tokens)")

        full_tokens = system_tokens + total_text_tokens + total_image_tokens

        # 当前轮 user 消息估算（含截图）
        current_user_tokens = 0
        current_user_has_screenshot = reason_screenshots.image_count > 0
        current_user_image_tokens = self.estimate_current_reason_image_tokens()
        if current_user_has_screenshot:
            current_user_tokens = current_user_image_tokens + 50  # 截图估算 + 文本

        estimated_api_total = full_tokens + current_user_tokens
        limit_status = "OK" if estimated_api_total <= 18000 else f"OVER by {estimated_api_total - 18000}"

        # 注意：截图只发在当前 user 消息中，不进入 context 历史
        # context_total 是历史文本+历史截图，current_user 是当前轮截图+文本
        scheduler_logger.info(
            f"[TOKEN WINDOW] device={self.device_id}, task={self.task_id}, step={self.current_step}, "
            f"msgs={len(all_msgs)}, "
            f"system={system_tokens}, "
            f"context_text={total_text_tokens}, context_image={total_image_tokens}, "
            f"context_total={full_tokens}, "
            f"current_user_text=~50, current_user_has_screenshot={current_user_has_screenshot}, "
            f"current_user_image_count={reason_screenshots.image_count}, "
            f"current_user_screenshot_est={current_user_image_tokens}, "
            f"warn_unchanged={prompt_debug['warn_unchanged']}, "
            f"ESTIMATED_API_TOTAL={estimated_api_total} [{limit_status}], "
            f"breakdown=[{', '.join(msg_breakdown[:3])}{'...' if len(msg_breakdown) > 3 else ''}]"
        )

        try:
            # 调用模型（同步调用，在线程中执行）
            scheduler_logger.info(
                f"[REASON PROMPT] device={self.device_id}, task={self.task_id}, step={self.current_step}, "
                f"mode={prompt_debug['mode']}, image_count={prompt_debug['image_count']}, "
                f"warn_unchanged={prompt_debug['warn_unchanged']}"
            )

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
            self.consume_reflection_prompt()

            # 解析思考和动作
            reasoning, action_text = self._parse_action(content)
            action_dict = self._parse_action_to_dict(action_text)

            scheduler_logger.info(f"[REASON] model response: device={self.device_id}, task={self.task_id}, latency={latency:.1f}s, content_len={len(content)}, preview={content[:100]}")

            # Persist AI reasoning result to chat_history.json
            from src.services.file_storage import file_storage
            file_storage.append_chat_message(self.device_id, {
                "id": f"msg_{self.task_id}_reason_{self.current_step}",
                "role": "assistant",
                "content": content,
                "created_at": datetime.now().isoformat(),
                "task_id": self.task_id,
                "step_number": self.current_step,
                "phase": "reason",
                "thinking": reasoning,
            })

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
                    ack_timeout_seconds=settings.REACT_ACK_TIMEOUT,
                    observe_timeout_seconds=settings.REACT_OBSERVE_TIMEOUT,
                    session_id=self.session_id or self.task_id,
                    run_id=self.run_id or "",
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
            scheduler_logger.error(
                f"[ACT] timeout: device={self.device_id}, task={self.task_id}, "
                f"timeout={settings.REACT_OBSERVE_TIMEOUT}"
            )
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
                self.react_records[-1].screenshot_path = screenshot_path
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
            action=action,
            before_action_screenshot=self.get_latest_screenshot(),
            before_action_screenshot_path=self.get_latest_screenshot_path(),
        )
        self.react_records.append(record)
        self.current_step = len(self.react_records)
        self.phase = TaskPhase.ACT
        self.last_active_at = time.time()

        scheduler_logger.debug(
            f"[REASON] reason phase completed: device={self.device_id}, task={self.task_id}, "
            f"step_number={record.step_number}, action_type={action.get('action')}, "
            f"reasoning_preview={reasoning[:200] if reasoning else ''}, "
            f"has_before_action_screenshot={bool(record.before_action_screenshot)}"
        )

        # Save react record to file storage
        try:
            from src.services.file_storage import file_storage
            file_storage.append_react_record(self.device_id, {
                "step_number": record.step_number,
                "reasoning": reasoning,
                "action": action,
                "before_action_screenshot": record.before_action_screenshot_path,
                "phase": "reason",
            })
        except Exception as e:
            scheduler_logger.warning(f"[REASON] Failed to save react record: {e}")

    def get_reason_input_screenshots(self) -> ReasonInputScreenshots:
        return ReasonInputScreenshots.from_task(self)

    def build_reason_user_message(self) -> dict[str, Any]:
        screenshot_inputs = self.get_reason_input_screenshots()
        prompt_prefix = screenshot_inputs.build_prompt_prefix()
        user_text = f"用户指令: {self.instruction}\n{prompt_prefix}"
        if self.reflection_prompt:
            user_text = f"{self.reflection_prompt}\n\n{user_text}"
        if self.initial_observation and not self.react_records:
            user_text = f"初始观察: {self.initial_observation}\n\n{user_text}"

        content_parts = screenshot_inputs.build_content_parts()
        content_parts.append({"type": "text", "text": user_text})
        return {
            "role": "user",
            "content": content_parts,
        }

    def consume_reflection_prompt(self):
        self.reflection_prompt = ""

    def get_reason_prompt_for_tests(self) -> dict[str, Any]:
        return self.build_reason_user_message()

    def get_reason_prompt_state_for_tests(self) -> dict[str, Any]:
        return self.get_reason_prompt_debug_info()

    def get_reason_prompt_urls_for_tests(self) -> list[str]:
        return [
            part.get("image_url", {}).get("url", "")
            for part in self.build_reason_user_message().get("content", [])
            if part.get("type") == "image_url" and isinstance(part.get("image_url"), dict)
        ]

    def get_reason_prompt_text_for_tests(self) -> str:
        for part in self.build_reason_user_message().get("content", []):
            if part.get("type") == "text":
                return str(part.get("text", ""))
        return ""

    def get_reason_prompt_count_for_tests(self) -> int:
        return self.get_current_reason_image_count()

    def get_reason_prompt_warning_for_tests(self) -> bool:
        return self.should_warn_reason_unchanged()

    def get_reason_prompt_mode_for_tests(self) -> str:
        return self.get_reason_prompt_debug_info()["mode"]

    def get_reason_prompt_image_parts_for_tests(self) -> list[dict[str, Any]]:
        return [
            part
            for part in self.build_reason_user_message().get("content", [])
            if part.get("type") == "image_url"
        ]

    def get_reason_prompt_pair_state_for_tests(self) -> dict[str, Any]:
        record = self.react_records[-1] if self.react_records else None
        return {
            "step_number": record.step_number if record else None,
            "has_before": bool(record and record.before_action_screenshot),
            "has_after": bool(record and record.screenshot),
            "unchanged": record.screenshot_unchanged if record else None,
        }

    def estimate_current_reason_image_tokens(self) -> int:
        return self.get_reason_input_screenshots().estimate_image_tokens()

    def get_current_reason_image_count(self) -> int:
        return self.get_reason_input_screenshots().image_count

    def should_warn_reason_unchanged(self) -> bool:
        return self.get_reason_input_screenshots().unchanged_warning

    def get_reason_prompt_debug_info(self) -> dict[str, Any]:
        screenshot_inputs = self.get_reason_input_screenshots()
        record = self.react_records[-1] if self.react_records else None
        return {
            "mode": "comparison" if screenshot_inputs.has_comparison_pair else ("single" if screenshot_inputs.latest_screenshot else "none"),
            "image_count": screenshot_inputs.image_count,
            "warn_unchanged": screenshot_inputs.unchanged_warning,
            "step_number": record.step_number if record else None,
        }

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "task_id": self.task_id,
            "instruction": self.instruction,
            "mode": self.mode,
            "current_step": self.current_step,
            "max_steps": self.max_steps,
            "phase": self.phase.value,
            "status": self.status.value,
            "react_records": [asdict(record) for record in self.react_records],
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DeviceTask':
        task = cls(
            device_id=data["device_id"],
            task_id=data["task_id"],
            instruction=data["instruction"],
            mode=data.get("mode", "normal"),
        )
        task.current_step = data.get("current_step", 0)
        task.max_steps = data.get("max_steps", 100)
        task.phase = TaskPhase(data.get("phase", "idle"))
        task.status = TaskStatus(data.get("status", "pending"))
        task.created_at = data.get("created_at", time.time())
        task.last_active_at = data.get("last_active_at", time.time())
        task.react_records = [ReActRecord(**record) for record in data.get("react_records", [])]
        task.context = DeviceTaskContext(system_prompt=task.get_system_prompt())
        return task

    def add_callback(self, callback: ReActCallback):
        self.callbacks.append(callback)

    def remove_callback(self, callback: ReActCallback):
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    def to_session_snapshot(self) -> dict:
        latest_screenshot = self.get_latest_screenshot()
        latest_observation = self.get_latest_observation()
        return {
            "task_id": self.task_id,
            "instruction": self.instruction,
            "status": self.status.value,
            "phase": self.phase.value,
            "current_step": self.current_step,
            "max_steps": self.max_steps,
            "latest_screenshot": latest_screenshot,
            "latest_observation": latest_observation,
            "react_records": [asdict(record) for record in self.react_records],
        }

    def restore_initial_observe(self, screenshot: str, observation: str, screenshot_path: Optional[str] = None):
        self.initial_screenshot = screenshot or ""
        self.initial_observation = observation or ""
        self.initial_screenshot_path = screenshot_path
        self.initial_observe_success = bool(screenshot or observation)
        self.last_active_at = time.time()

    def get_bootstrap_step_event(self) -> Optional[ReActStepEvent]:
        if not (self.initial_screenshot or self.initial_observation):
            return None
        return ReActStepEvent(
            device_id=self.device_id,
            task_id=self.task_id,
            step_number=0,
            phase="observe",
            reasoning="",
            action={},
            result=self.initial_observation,
            screenshot=self.initial_screenshot,
            success=self.initial_observe_success,
            error=None if self.initial_observe_success else self.initial_observation,
            error_type=None if self.initial_observe_success else ReActErrorType.OBSERVE_ERROR.value,
        )

    def has_bootstrap_data(self) -> bool:
        return bool(self.initial_screenshot or self.initial_observation)

    def clear_bootstrap_data(self):
        self.initial_screenshot = ""
        self.initial_observation = ""
        self.initial_screenshot_path = None
        self.initial_observe_success = False

    def __repr__(self) -> str:
        return (
            f"DeviceTask(device_id={self.device_id!r}, task_id={self.task_id!r}, "
            f"status={self.status.value!r}, phase={self.phase.value!r}, step={self.current_step})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def clone_without_runtime(self) -> "DeviceTask":
        cloned = DeviceTask.from_dict(self.to_dict())
        cloned.callbacks = []
        cloned.status_callback = None
        cloned.step_callback = None
        cloned.action_executor = None
        cloned._action_router = None
        cloned._model_client = None
        return cloned

    def to_safe_debug_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "phase": self.phase.value,
            "current_step": self.current_step,
            "records": len(self.react_records),
            "has_initial_screenshot": bool(self.initial_screenshot),
            "has_initial_observation": bool(self.initial_observation),
        }

    def debug_summary(self) -> str:
        return json.dumps(self.to_safe_debug_dict(), ensure_ascii=False)

    def reset_runtime_clients(self):
        self._action_router = None
        self._model_client = None

    def touch(self):
        self.last_active_at = time.time()

    def clear_reflection_prompt(self):
        self.reflection_prompt = ""

    def set_reflection_prompt(self, prompt: str):
        self.reflection_prompt = prompt or ""

    def append_context_message(self, role: str, content: Any):
        self.context.add_message(role, content)

    def truncate_context(self, max_tokens: int = 18000):
        self.context.truncate(max_tokens=max_tokens)

    def context_to_api_format(self) -> list[dict]:
        return self.context.to_api_format()

    def has_react_records(self) -> bool:
        return bool(self.react_records)

    def get_latest_record(self) -> Optional[ReActRecord]:
        return self.react_records[-1] if self.react_records else None

    def get_latest_step_number(self) -> int:
        record = self.get_latest_record()
        return record.step_number if record else 0

    def get_latest_action(self) -> dict:
        record = self.get_latest_record()
        return record.action if record else {}

    def get_latest_reasoning(self) -> str:
        record = self.get_latest_record()
        return record.reasoning if record else ""

    def get_latest_action_result(self) -> str:
        record = self.get_latest_record()
        return record.action_result if record else ""

    def get_latest_record_success(self) -> Optional[bool]:
        record = self.get_latest_record()
        return record.success if record else None

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
    MAX_AI_RETRIES = settings.REACT_AI_MAX_RETRIES
    AI_TIMEOUT = settings.REACT_AI_TIMEOUT
    MAX_ACK_RETRIES = settings.REACT_ACK_MAX_RETRIES
    ACK_RETRY_INTERVAL = settings.REACT_ACK_RETRY_INTERVAL

    def __init__(
        self,
        core_threads: int = settings.REACT_CORE_THREADS,
        max_threads: int = settings.REACT_MAX_THREADS,
        reason_timeout: int = settings.REACT_REASON_TIMEOUT,
        observe_timeout: float = settings.REACT_OBSERVE_TIMEOUT,
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

        # 持久会话注册表: device_id -> {session_id, session_started_at, run_count}
        # 每次 submit_task 在同一设备上创建新 run，但 session_id 保持不变
        self._device_sessions: dict[str, dict] = {}

        # 设备执行所有权 (device_id -> execution_token)
        self._device_execution_tokens: dict[str, int] = {}
        self._execution_token_counter = 0

        # 上次记录的队列状态，用于减少重复日志
        self._last_queue_state: Optional[str] = None

        # 运行中的任务（thread_id -> device_id）
        self._running_tasks: dict[int, str] = {}
        self._running_lock = Lock()

        # 待确认的操作
        self._waiting_confirmations: dict[str, dict] = {}

        # 首轮 bootstrap observe 等待器
        self._bootstrap_waiters: dict[str, asyncio.Future] = {}
        # 首轮 bootstrap ACK 等待器
        self._bootstrap_ack_waiters: dict[str, asyncio.Future] = {}
        # 首轮 bootstrap 截图请求的 msg_id，用于匹配 ACK
        self._bootstrap_screenshot_msg_ids: dict[str, str] = {}

        # WebSocket hub (用于推送)
        self._ws_hub = None

        # 主线程 event loop (用于跨线程广播)
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        # 是否正在运行
        self._running = False

        scheduler_logger.info(f"ReActScheduler initialized: core_threads={core_threads}, max_threads={max_threads}")

    def _build_session_system_prompt(self) -> str:
        """构建会话级固定 system prompt，不绑定单次 task instruction。"""
        today = datetime.today()
        formatted_date = today.strftime("%Y-%m-%d, %A")

        return f"""当前日期: {formatted_date}
# Setup
你是一个专业的Android手机操作助手。在每一步中，你会收到手机屏幕截图和用户指令。

# 你的任务
你必须根据用户指令执行操作。最新的用户指令会在 user 消息中提供。
你需要结合当前会话中保留的历史上下文、最新截图和最新用户指令，继续执行或完成任务。

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
7. 当上下文中出现 [ObserveError] 标签时，表示上一条 action 命令执行失败或未成功落地；你必须把它视为纠错反馈，结合最新截图重新判断，不要机械重复同一错误动作"""

    def get_or_create_session_context(self, device_id: str) -> DeviceTaskContext:
        """
        获取或创建会话级上下文（跨任务持久）。

        1. 如果内存中已有 context，直接返回（最快路径）。
        2. 否则从 chat_history.json 重建 messages，存入内存后返回。
        """
        sess = self._device_sessions.get(device_id)
        if sess is not None and sess.get("context") is not None:
            scheduler_logger.debug(f"[SESSION] Reusing existing context for device={device_id}")
            return sess["context"]

        from src.services.file_storage import file_storage
        context = DeviceTaskContext(system_prompt=self._build_session_system_prompt())

        persisted = file_storage.load_chat_history(device_id)
        for msg in persisted:
            role = msg.get("role", "")
            if role in ("user", "assistant", "tool-use"):
                context.messages.append({"role": role, "content": msg.get("content", "")})

        scheduler_logger.info(f"[SESSION] Loaded session context for device={device_id}, messages={len(context.messages)}")

        if device_id in self._device_sessions:
            self._device_sessions[device_id]["context"] = context
        else:
            self._device_sessions[device_id] = {
                "session_id": device_id,
                "session_started_at": time.time(),
                "run_count": 0,
                "context": context,
            }
        return context

    def clear_session_context(self, device_id: str):
        """
        清除会话上下文：内存置 None，文件清空。
        用于用户主动点"清空上下文"按钮。
        """
        if device_id in self._device_sessions:
            self._device_sessions[device_id]["context"] = None

        from src.services.file_storage import file_storage
        file_storage.clear_context(device_id)
        file_storage.save_chat_history(device_id, [])
        scheduler_logger.info(f"[SESSION] Session context cleared for device={device_id}")

    def set_ws_hub(self, hub):
        """设置WebSocket Hub用于推送"""
        self._ws_hub = hub
        # 获取主线程的 event loop 用于跨线程调度
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    async def _broadcast(self, coro, *args, **kwargs):
        """跨线程安全地广播到 WebSocket"""
        if not self._ws_hub:
            return
        if self._main_loop is None:
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        if self._main_loop.is_closed():
            return
        # 在主线程的 loop 中调度协程
        full_coro = coro(self._ws_hub, *args, **kwargs)
        asyncio.run_coroutine_threadsafe(full_coro, self._main_loop)

    def _safe_broadcast(self, coro_factory):
        """线程安全地调度广播协程到主线程loop"""
        if not self._ws_hub:
            return
        if self._main_loop is None:
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        if self._main_loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(coro_factory(self._ws_hub), self._main_loop)

    # === 线程安全的广播方法 ===
    def broadcast_agent_progress(self, **kwargs):
        self._safe_broadcast(lambda hub: hub.broadcast_agent_progress(**kwargs))

    def broadcast_agent_phase_start(self, task_id, device_id, phase, step_number):
        self._safe_broadcast(lambda hub: hub.broadcast_agent_phase_start(device_id, task_id, phase, step_number))

    def broadcast_agent_step(self, task_id, device_id, step, **kwargs):
        self._safe_broadcast(lambda hub: hub.broadcast_agent_step(task_id, device_id, step, **kwargs))

    def broadcast_agent_status(self, **kwargs):
        # 透传所有参数到 hub 的 broadcast_agent_status
        # hub签名: broadcast_agent_status(device_id, session_id="", status="", message="", data=None)
        self._safe_broadcast(lambda hub: hub.broadcast_agent_status(**kwargs))

    def send_to_device(self, device_id, message):
        """同步发送消息到设备（线程安全）"""
        if not self._ws_hub:
            return False
        if self._main_loop is None:
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                return False
        if self._main_loop.is_closed():
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._ws_hub.send_to_device(device_id, message), self._main_loop
        )
        try:
            return future.result(timeout=5)
        except Exception:
            return False

    def submit_task(
        self,
        device_id: str,
        task_id: str,
        instruction: str,
        mode: str = "normal",
        max_steps: int = 100,
        max_observe_error_retries: int = settings.REACT_MAX_OBSERVE_ERROR_RETRIES,
        action_executor: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        step_callback: Optional[Callable] = None,
        callbacks: Optional[list] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> DeviceTask:
        """提交新任务到队列尾部。

        会话/运行语义:
        - session_id: 持久会话ID，等于传入的 task_id（兼容别名）。同一设备再次调用时复用。
        - run_id: 每次调用新建，用于 ACK/observe/interrupt/confirm 的精确归属。
        """
        now = time.time()

        # 建立或复用设备会话
        if device_id in self._device_sessions:
            sess = self._device_sessions[device_id]
            # 同一设备的新 run，复用 session_id
            sess["run_count"] = sess.get("run_count", 0) + 1
            session_id = sess["session_id"]
        else:
            # 首个 run，为该设备创建新 session
            session_id = session_id or task_id
            self._device_sessions[device_id] = {
                "session_id": session_id,
                "session_started_at": now,
                "run_count": 1,
            }
            scheduler_logger.info(f"[SESSION] New session created: device={device_id}, session_id={session_id}")

        # 每次运行生成新的 run_id
        run_id = f"run_{uuid.uuid4().hex[:12]}"

        scheduler_logger.info(
            f"[SUBMIT] device={device_id}, task={task_id}, session={session_id}, run={run_id}, "
            f"instruction={instruction[:30]}..."
        )

        task = DeviceTask(
            device_id=device_id,
            task_id=task_id,
            instruction=instruction,
            mode=mode,
            max_steps=max_steps,
            max_observe_error_retries=max_observe_error_retries,
            action_executor=action_executor,
            status_callback=status_callback,
            step_callback=step_callback,
            callbacks=callbacks or [],
            session_id=session_id,
            run_id=run_id,
            session_started_at=now,
            run_started_at=now,
        )
        # Use session-level context (persists across tasks on the same device)
        session_context = self.get_or_create_session_context(device_id)
        task.context = session_context
        task.react_records = []
        task.initial_screenshot = ""
        task.initial_observation = ""
        task.initial_screenshot_path = None
        task.initial_observe_success = False
        task.current_step = 0
        task.phase = TaskPhase.REASON
        task.status = TaskStatus.PENDING
        task.session_status = SessionStatus.WAIT_FOR_PUSH
        task.last_active_at = time.time()

        with self._queue_lock:
            scheduler_logger.info(
                f"[SUBMIT] Queue check: device={device_id}, in_device_tasks={device_id in self._device_tasks}, "
                f"queue_before={[d for d in self._task_queue]}, tokens={dict(self._device_execution_tokens)}"
            )
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
            # Append user instruction to chat history (preserving prior messages)
            file_storage.append_chat_message(device_id, {
                "id": f"msg_{task_id}_user",
                "role": "user",
                "content": instruction,
                "created_at": datetime.now().isoformat(),
                "task_id": task_id,
            })
        except Exception as e:
            scheduler_logger.warning(f"[SUBMIT] Failed to save initial context: {e}")

        return task

    def _still_owns_task(self, device_id: str, task: DeviceTask, execution_token: int) -> bool:
        """检查当前 worker 是否仍持有该设备任务的执行权。"""
        with self._queue_lock:
            current_task = self._device_tasks.get(device_id)
            return (
                current_task is task
                and task.is_active
                and self._device_execution_tokens.get(device_id) == execution_token
            )

    def _log_stale_worker_exit(self, device_id: str, task: DeviceTask, execution_token: int, stage: str):
        """记录 worker 失去执行权后的退出日志。"""
        with self._queue_lock:
            current_task = self._device_tasks.get(device_id)
            current_task_id = current_task.task_id if current_task else None
            current_token = self._device_execution_tokens.get(device_id)

        scheduler_logger.info(
            f"[OWNERSHIP] stale worker exit: device={device_id}, stage={stage}, "
            f"task={task.task_id}, token={execution_token}, current_task={current_task_id}, current_token={current_token}"
        )

    def get_next_task(self) -> Optional[tuple[DeviceTask, int]]:
        """从队列头部获取任务并获取设备执行权。"""
        with self._queue_lock:
            while self._task_queue:
                device_id = self._task_queue.pop(0)
                task = self._device_tasks.get(device_id)
                if not task or not task.is_active:
                    state_str = f"Skipping device={device_id}, is_active={task.is_active if task else 'task_not_found'}"
                    if state_str != self._last_queue_state:
                        scheduler_logger.debug(f"[GET_TASK] {state_str}")
                        self._last_queue_state = state_str
                    continue

                if device_id in self._device_execution_tokens:
                    state_str = f"Skipping device={device_id}, already_owned token={self._device_execution_tokens[device_id]}"
                    if state_str != self._last_queue_state:
                        scheduler_logger.debug(f"[GET_TASK] {state_str}")
                        self._last_queue_state = state_str
                    continue

                self._execution_token_counter += 1
                execution_token = self._execution_token_counter
                self._device_execution_tokens[device_id] = execution_token

                state_str = (
                    f"Acquired device={device_id}, task={task.task_id}, status={task.status.value}, "
                    f"phase={task.phase.value}, token={execution_token}"
                )
                if state_str != self._last_queue_state:
                    scheduler_logger.info(f"[GET_TASK] {state_str}")
                    self._last_queue_state = state_str
                return task, execution_token

            if self._last_queue_state != "Queue empty":
                scheduler_logger.info("[GET_TASK] Queue empty, no tasks available")
                self._last_queue_state = "Queue empty"
            return None

    def requeue_task(self, device_id: str, task: DeviceTask, execution_token: int) -> bool:
        """任务完成一轮后在仍持有执行权时放回队列尾部。"""
        with self._queue_lock:
            current_task = self._device_tasks.get(device_id)
            current_token = self._device_execution_tokens.get(device_id)
            if current_task is not task or not task.is_active or current_token != execution_token:
                scheduler_logger.info(
                    f"[REQUEUE] skipped stale owner: device={device_id}, task={task.task_id}, token={execution_token}, "
                    f"current_task={current_task.task_id if current_task else None}, current_token={current_token}"
                )
                return False

            task.context.truncate(max_tokens=18000)
            if device_id in self._task_queue:
                scheduler_logger.info(
                    f"[REQUEUE] skipped duplicate queue entry: device={device_id}, task={task.task_id}, "
                    f"token={execution_token}, queue_size={len(self._task_queue)}"
                )
                return False

            self._task_queue.append(device_id)
            self._last_queue_state = None
            scheduler_logger.info(
                f"[REQUEUE] device={device_id} requeued to tail, queue_size={len(self._task_queue)}, "
                f"next_phase={task.phase.value}, token={execution_token}"
            )
            return True

    def remove_task(self, device_id: str):
        """移除任务"""
        with self._queue_lock:
            if device_id in self._device_tasks:
                del self._device_tasks[device_id]
            self._device_execution_tokens.pop(device_id, None)
            self._task_queue = [queued_device_id for queued_device_id in self._task_queue if queued_device_id != device_id]
            self._waiting_confirmations.pop(device_id, None)
            self._running_tasks.pop(device_id, None)
        bootstrap_waiter = self._bootstrap_waiters.pop(device_id, None)
        if bootstrap_waiter and not bootstrap_waiter.done():
            bootstrap_waiter.cancel()
        bootstrap_ack_waiter = self._bootstrap_ack_waiters.pop(device_id, None)
        if bootstrap_ack_waiter and not bootstrap_ack_waiter.done():
            bootstrap_ack_waiter.cancel()
        self._bootstrap_screenshot_msg_ids.pop(device_id, None)

    async def _request_bootstrap_screenshot(self, task: DeviceTask) -> tuple[str, str]:
        """请求首轮截图，并返回 (screenshot, observation)。"""
        if not self._ws_hub:
            raise ObserveException(ReActErrorType.OBSERVE_TIMEOUT, "Bootstrap screenshot hub unavailable")

        loop = asyncio.get_running_loop()
        observe_waiter = loop.create_future()
        previous_waiter = self._bootstrap_waiters.get(task.device_id)
        if previous_waiter and not previous_waiter.done():
            previous_waiter.cancel()
        self._bootstrap_waiters[task.device_id] = observe_waiter

        ack_waiter = loop.create_future()
        previous_ack_waiter = self._bootstrap_ack_waiters.get(task.device_id)
        if previous_ack_waiter and not previous_ack_waiter.done():
            previous_ack_waiter.cancel()
        self._bootstrap_ack_waiters[task.device_id] = ack_waiter

        from src.network.message_types import create_request_screenshot

        ws_message = create_request_screenshot(
            task.task_id,
            task.device_id,
            step_number=0,
            phase="observe",
            purpose="bootstrap",
        )
        message = ws_message.to_dict()
        # 记录 bootstrap 截图请求的 msg_id，用于匹配 ACK
        self._bootstrap_screenshot_msg_ids[task.device_id] = ws_message.msg_id

        scheduler_logger.info(
            f"[BOOTSTRAP] request_screenshot dispatched: device={task.device_id}, task={task.task_id}, "
            f"msg_id={ws_message.msg_id}, step=0"
        )

        sent = self.send_to_device(task.device_id, message)
        if not sent:
            self._bootstrap_waiters.pop(task.device_id, None)
            self._bootstrap_ack_waiters.pop(task.device_id, None)
            self._bootstrap_screenshot_msg_ids.pop(task.device_id, None)
            raise ObserveException(ReActErrorType.OBSERVE_TIMEOUT, "Bootstrap screenshot request failed")

        ack_timeout = min(float(task.observe_timeout), settings.REACT_ACK_TIMEOUT)

        try:
            try:
                await asyncio.wait_for(ack_waiter, timeout=ack_timeout)
            except asyncio.TimeoutError as exc:
                self._bootstrap_screenshot_msg_ids.pop(task.device_id, None)
                raise ObserveException(ReActErrorType.ACK_TIMEOUT, "Bootstrap screenshot ACK timeout") from exc

            try:
                result = await asyncio.wait_for(observe_waiter, timeout=task.observe_timeout)
            except asyncio.TimeoutError as exc:
                raise ObserveException(ReActErrorType.OBSERVE_TIMEOUT, "Bootstrap screenshot timeout") from exc
        finally:
            current_ack_waiter = self._bootstrap_ack_waiters.get(task.device_id)
            if current_ack_waiter is ack_waiter:
                self._bootstrap_ack_waiters.pop(task.device_id, None)
            current_waiter = self._bootstrap_waiters.get(task.device_id)
            if current_waiter is observe_waiter:
                self._bootstrap_waiters.pop(task.device_id, None)
            self._bootstrap_screenshot_msg_ids.pop(task.device_id, None)

        return result

    async def _ensure_bootstrap_observation(self, device_id: str, task: DeviceTask, execution_token: int) -> bool:
        """首轮推理前确保已有 bootstrap 截图。"""
        if task.current_step > 0 or task.has_initial_screenshot() or task.react_records:
            return True

        if self._ws_hub:
            self.broadcast_agent_progress(
                task_id=task.task_id,
                device_id=device_id,
                step_number=0,
                phase="observe",
                stage="waiting_ack",
                message="等待 bootstrap screenshot ACK",
                session_id=task.session_id or task.task_id,
                run_id=task.run_id,
            )
        await self._emit_phase_start(device_id, task.task_id, "observe", 0)
        if not await self._guard_task_ownership(device_id, task, execution_token, "after_bootstrap_phase_start"):
            return False

        try:
            screenshot, observation = await self._request_bootstrap_screenshot(task)
        except ObserveException as exc:
            if self._ws_hub:
                if exc.error_type == ReActErrorType.ACK_TIMEOUT:
                    stage = "ack_timeout"
                elif exc.error_type == ReActErrorType.ACK_REJECTED:
                    stage = "ack_rejected"
                elif exc.error_type == ReActErrorType.OBSERVE_ERROR:
                    stage = "observe_received"
                else:
                    stage = "observe_timeout"
                self.broadcast_agent_progress(
                    task_id=task.task_id,
                    device_id=device_id,
                    step_number=0,
                    phase="observe",
                    stage=stage,
                    message=exc.message,
                    error=exc.message,
                    error_type=exc.error_type.value,
                    success=False,
                    session_id=task.session_id or task.task_id,
                    run_id=task.run_id,
                )
            raise

        if not await self._guard_task_ownership(device_id, task, execution_token, "after_bootstrap_observe"):
            return False

        task.initial_screenshot = screenshot or ""
        task.initial_observation = observation or ""
        task.initial_observe_success = True
        task.phase = TaskPhase.OBSERVE
        task.session_status = SessionStatus.WAIT_FOR_PUSH
        task.last_active_at = time.time()

        if self._ws_hub:
            self.broadcast_agent_progress(
                task_id=task.task_id,
                device_id=device_id,
                step_number=0,
                phase="observe",
                stage="observe_received",
                message="初始截图已收到",
                result=task.initial_observation,
                screenshot=task.initial_screenshot,
                success=True,
                session_id=task.session_id or task.task_id,
                run_id=task.run_id,
            )
        return True

    def get_bootstrap_waiting_device(self, task_id: str, device_id: str) -> bool:
        """判断任务是否正等待首轮 bootstrap 截图。"""
        waiter = self._bootstrap_waiters.get(device_id)
        task = self._device_tasks.get(device_id)
        return bool(waiter and not waiter.done() and task and task.task_id == task_id and task.is_active)

    def consume_bootstrap_observe_result(
        self,
        task_id: str,
        device_id: str,
        screenshot: str,
        observation: str,
        *,
        success: bool = True,
        error: Optional[str] = None,
    ) -> bool:
        """消费 bootstrap observe_result，不走普通 pending round。"""
        waiter = self._bootstrap_waiters.get(device_id)
        task = self._device_tasks.get(device_id)
        if not waiter or waiter.done() or not task or task.task_id != task_id or not task.is_active:
            return False

        if not success:
            waiter.set_exception(
                ObserveException(
                    ReActErrorType.OBSERVE_ERROR,
                    error or observation or "Bootstrap screenshot failed",
                )
            )
            return True

        waiter.set_result((screenshot, observation))
        return True

    async def handle_bootstrap_ack(
        self,
        device_id: str,
        ref_msg_id: str,
        *,
        accepted: bool = True,
        error: Optional[str] = None,
    ) -> bool:
        """处理 bootstrap 截图请求的 ACK，并发射 canonical transport milestones。"""
        expected_msg_id = self._bootstrap_screenshot_msg_ids.get(device_id)
        if not expected_msg_id or expected_msg_id != ref_msg_id:
            return False

        self._bootstrap_screenshot_msg_ids.pop(device_id, None)

        task = self._device_tasks.get(device_id)
        task_id = task.task_id if task else ""
        session_id = getattr(task, "session_id", None) or task_id
        run_id = getattr(task, "run_id", None)
        ack_waiter = self._bootstrap_ack_waiters.get(device_id)
        waiter = self._bootstrap_waiters.get(device_id)

        if not accepted:
            message = error or "Bootstrap screenshot ACK rejected"
            if ack_waiter and not ack_waiter.done():
                ack_waiter.set_exception(ObserveException(ReActErrorType.ACK_REJECTED, message))
            if waiter and not waiter.done():
                waiter.set_exception(ObserveException(ReActErrorType.ACK_REJECTED, message))
            if self._ws_hub:
                self.broadcast_agent_progress(
                    task_id=task_id,
                    device_id=device_id,
                    step_number=0,
                    phase="observe",
                    stage="ack_rejected",
                    message=message,
                    error=message,
                    error_type=ReActErrorType.ACK_REJECTED.value,
                    success=False,
                    session_id=session_id,
                    run_id=run_id,
                )
            scheduler_logger.warning(
                f"[BOOTSTRAP ACK] ack rejected: device={device_id}, task={task_id}, error={message}"
            )
            return True

        if ack_waiter and not ack_waiter.done():
            ack_waiter.set_result(True)

        if self._ws_hub:
            self.broadcast_agent_progress(
                task_id=task_id,
                device_id=device_id,
                step_number=0,
                phase="observe",
                stage="ack_received",
                message="Bootstrap screenshot ACK 已收到",
                session_id=session_id,
                run_id=run_id,
            )
            self.broadcast_agent_progress(
                task_id=task_id,
                device_id=device_id,
                step_number=0,
                phase="observe",
                stage="waiting_observe",
                message="等待 bootstrap screenshot observe_result",
                session_id=session_id,
                run_id=run_id,
            )

        scheduler_logger.info(
            f"[BOOTSTRAP ACK] canonical ACK milestones emitted: device={device_id}, task={task_id}"
        )
        return True

    def _release_execution_token(self, device_id: str, execution_token: int):
        """仅在 token 匹配时释放设备执行权。"""
        with self._queue_lock:
            current_token = self._device_execution_tokens.get(device_id)
            if current_token == execution_token:
                del self._device_execution_tokens[device_id]
                scheduler_logger.debug(
                    f"[OWNERSHIP] released execution token: device={device_id}, token={execution_token}"
                )
            else:
                scheduler_logger.debug(
                    f"[OWNERSHIP] skip release: device={device_id}, token={execution_token}, current_token={current_token}"
                )

    def _set_task_running_if_owned(self, device_id: str, task: DeviceTask, execution_token: int) -> bool:
        """在持有执行权时将任务置为运行中。"""
        with self._queue_lock:
            current_task = self._device_tasks.get(device_id)
            current_token = self._device_execution_tokens.get(device_id)
            if current_task is not task or not task.is_active or current_token != execution_token:
                return False
            task.status = TaskStatus.RUNNING
            return True

    async def _guard_task_ownership(self, device_id: str, task: DeviceTask, execution_token: int, stage: str) -> bool:
        """校验当前 worker 是否仍持有设备任务执行权。"""
        if self._still_owns_task(device_id, task, execution_token):
            return True
        self._log_stale_worker_exit(device_id, task, execution_token, stage)
        return False

    async def _emit_complete_if_owned(self, task: DeviceTask, final_reasoning: str, execution_token: int) -> bool:
        if not await self._guard_task_ownership(task.device_id, task, execution_token, "before_complete"):
            return False
        await self._emit_complete(task, final_reasoning)
        return True

    async def _emit_failed_if_owned(
        self,
        task: DeviceTask,
        message: str,
        error_type: ReActErrorType,
        execution_token: int,
        final_reasoning: str = "",
    ) -> bool:
        if not await self._guard_task_ownership(task.device_id, task, execution_token, "before_failed"):
            return False
        await self._emit_failed(task, message, error_type, final_reasoning)
        return True

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
            self.broadcast_agent_phase_start(
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
            session_id = task.session_id or task.task_id
            self.broadcast_agent_step(
                task_id=task.task_id,
                device_id=task.device_id,
                step=event.__dict__,
                step_type="agent_step",
                session_id=session_id,
                run_id=task.run_id,
            )

    async def _emit_complete(self, task: DeviceTask, final_reasoning: str):
        """发送任务完成事件"""
        task.status = TaskStatus.COMPLETED
        task.session_status = SessionStatus.FINISHED

        session_id = task.session_id or task.task_id
        event = ReActTaskEvent(
            device_id=task.device_id,
            task_id=task.task_id,
            status="completed",
            message="Task completed",
            final_reasoning=final_reasoning,
            error_type=None,
            session_id=session_id,
            run_id=task.run_id,
        )

        for cb in task.callbacks:
            try:
                if hasattr(cb, 'on_task_complete'):
                    await cb.on_task_complete(event)
            except Exception as e:
                scheduler_logger.warning(f"[CALLBACK] on_task_complete error: {e}")

        if self._ws_hub:
            self.broadcast_agent_status(
                device_id=task.device_id,
                session_id=session_id,
                status="completed",
                message="Task completed",
                data={
                    "task_id": task.task_id,
                    "session_id": session_id,
                    "run_id": task.run_id,
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

        session_id = task.session_id or task.task_id
        event = ReActTaskEvent(
            device_id=task.device_id,
            task_id=task.task_id,
            status="failed",
            message=message,
            final_reasoning=final_reasoning,
            error_type=error_type.value if error_type else None,
            session_id=session_id,
            run_id=task.run_id,
        )

        for cb in task.callbacks:
            try:
                if hasattr(cb, 'on_task_failed'):
                    await cb.on_task_failed(event)
            except Exception as e:
                scheduler_logger.warning(f"[CALLBACK] on_task_failed error: {e}")

        if self._ws_hub:
            self.broadcast_agent_status(
                device_id=task.device_id,
                session_id=session_id,
                status="failed",
                message=message,
                data={
                    "task_id": task.task_id,
                    "session_id": session_id,
                    "run_id": task.run_id,
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

    async def run_one_cycle(self, device_id: str, task: DeviceTask, execution_token: int) -> bool:
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
        if not await self._guard_task_ownership(device_id, task, execution_token, "cycle_start"):
            return True

        scheduler_logger.info(
            f"[CYCLE] cycle starting: device={device_id}, task={task.task_id}, "
            f"current_step={task.current_step + 1}, phase={task.phase.value}, "
            f"queue_size={len(self._task_queue)}, total_records={len(task.react_records)}, token={execution_token}"
        )

        try:
            if not await self._ensure_bootstrap_observation(device_id, task, execution_token):
                return True
            next_step_number = task.current_step + 1

            await self._emit_phase_start(device_id, task.task_id, "reason", next_step_number)
            if not await self._guard_task_ownership(device_id, task, execution_token, "after_reason_phase_start"):
                return True

            # Emit reason_start milestone before model reasoning
            if self._ws_hub:
                self.broadcast_agent_progress(
                    task_id=task.task_id,
                    device_id=device_id,
                    step_number=next_step_number,
                    phase="reason",
                    stage="reason_start",
                    message="开始调用模型",
                    session_id=task.session_id or task.task_id,
                    run_id=task.run_id,
                )

            reasoning, action, raw_output = await self._reason_with_retry(task)
            if not await self._guard_task_ownership(device_id, task, execution_token, "after_reason"):
                return True

            scheduler_logger.info(
                f"[CYCLE] reason completed: device={device_id}, task={task.task_id}, "
                f"step={next_step_number}, action_guess={action.get('action')}, reasoning_preview={reasoning[:120] if reasoning else ''}"
            )

            if task._is_finish_action(action):
                scheduler_logger.info(
                    f"[CYCLE] finish action detected: device={device_id}, task={task.task_id}, step={next_step_number}"
                )
                await self._emit_complete_if_owned(task, reasoning, execution_token)
                return True

            parse_result = await self._action_parse_with_retry(task, reasoning, raw_output)
            if not await self._guard_task_ownership(device_id, task, execution_token, "after_parse"):
                return True

            scheduler_logger.info(
                f"[CYCLE] parse completed: device={device_id}, task={task.task_id}, "
                f"step={next_step_number}, parse_success={parse_result.success}, parsed_action={parse_result.action}"
            )

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
                task.reflection_prompt = parse_error_msg
                await self._emit_step(task, reasoning, action, parse_error_msg)
                if not await self._guard_task_ownership(device_id, task, execution_token, "before_requeue_after_unknown_action"):
                    return True
                self.requeue_task(device_id, task, execution_token)
                return False

            if not parse_result.success:
                await self._emit_failed_if_owned(
                    task,
                    parse_result.error or "Action parse failed",
                    parse_result.error_type or ReActErrorType.ACTION_PARSE_FAILED,
                    execution_token,
                    reasoning,
                )
                return True

            task.complete_reason(reasoning, parse_result.action)
            if not await self._guard_task_ownership(device_id, task, execution_token, "before_increment_version"):
                return True

            from src.services.device_status_manager import device_status_manager
            round_version = await device_status_manager.increment_version(device_id)

            if self._ws_hub:
                self.broadcast_agent_progress(
                    task_id=task.task_id,
                    device_id=device_id,
                    step_number=task.current_step,
                    phase="reason",
                    stage="reason_complete",
                    message="Reason 完成，动作已解析",
                    reasoning=reasoning,
                    action=parse_result.action,
                    version=round_version,
                    session_id=task.session_id or task.task_id,
                    run_id=task.run_id,
                )
            if not await self._guard_task_ownership(device_id, task, execution_token, "before_dispatch"):
                return True

            await self._emit_phase_start(device_id, task.task_id, "act", task.current_step)
            if not await self._guard_task_ownership(device_id, task, execution_token, "after_act_phase_start"):
                return True

            scheduler_logger.info(
                f"[CYCLE] dispatching action: device={device_id}, task={task.task_id}, "
                f"step={task.current_step}, version={round_version}, action={parse_result.action}"
            )
            dispatch_result = await self._dispatch_with_retry(task, parse_result.action, reasoning, round_version)
            if not await self._guard_task_ownership(device_id, task, execution_token, "after_dispatch"):
                return True

            result_text = dispatch_result.get("result", "")
            screenshot_b64 = dispatch_result.get("screenshot") or ""

            if result_text == "_finish_":
                await self._emit_complete_if_owned(task, reasoning, execution_token)
                return True

            task.complete_act(result_text)

            # Inject observation feedback into context so the model sees action results
            observe_parts = [f"动作: {json.dumps(parse_result.action, ensure_ascii=False)}"]
            if dispatch_result.get("success") is False:
                observe_parts.append("结果: 失败")
            else:
                observe_parts.append("结果: 成功")
            if result_text:
                observe_parts.append(f"观察: {result_text}")
            task.context.add_message("user", "\n".join(observe_parts))

            # Persist observe result to chat_history.json
            from src.services.file_storage import file_storage
            file_storage.append_chat_message(device_id, {
                "id": f"msg_{task.task_id}_observe_{task.current_step}",
                "role": "user",
                "content": "\n".join(observe_parts),
                "created_at": datetime.now().isoformat(),
                "task_id": task.task_id,
                "step_number": task.current_step,
                "phase": "observe",
            })

            await self._emit_phase_start(device_id, task.task_id, "observe", task.current_step)
            if not await self._guard_task_ownership(device_id, task, execution_token, "after_observe_phase_start"):
                return True

            await self._emit_step(
                task,
                reasoning,
                parse_result.action,
                result_text,
                screenshot_b64,
            )

            if task.current_step >= task.max_steps:
                await self._emit_complete_if_owned(task, "达到最大步数限制", execution_token)
                return True

            task.session_status = SessionStatus.WAIT_FOR_PUSH
            scheduler_logger.info(
                f"[CYCLE] cycle completed: device={device_id}, task={task.task_id}, "
                f"completed_step={task.current_step}, version={round_version}, "
                f"queue_size={len(self._task_queue)}, token={execution_token}"
            )
            if not await self._guard_task_ownership(device_id, task, execution_token, "before_requeue"):
                return True
            self.requeue_task(device_id, task, execution_token)
            return False

        except RemoteAPIException as e:
            scheduler_logger.error(
                f"[CYCLE] RemoteAPIException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            await self._emit_failed_if_owned(task, e.message, e.error_type, execution_token)
            return True

        except ActionParseException as e:
            scheduler_logger.error(
                f"[CYCLE] ActionParseException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            await self._emit_failed_if_owned(task, e.message, e.error_type, execution_token)
            return True

        except DeviceStatusException as e:
            scheduler_logger.error(
                f"[CYCLE] DeviceStatusException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            await self._emit_failed_if_owned(task, e.message, e.error_type, execution_token)
            return True

        except DispatchException as e:
            scheduler_logger.error(
                f"[CYCLE] DispatchException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            await self._emit_failed_if_owned(task, e.message, e.error_type, execution_token)
            return True

        except ObserveException as e:
            scheduler_logger.error(
                f"[CYCLE] ObserveException: device={device_id}, task={task.task_id}, "
                f"error_type={e.error_type}, message={e.message}"
            )
            if task.current_step > 0 and e.error_type == ReActErrorType.OBSERVE_ERROR:
                task.annotate_latest_record_observe_error(e.message)
                pending_decision = task.handle_observe_error(e.message)

                if pending_decision is not None and self._ws_hub:
                    self.broadcast_agent_progress(
                        task_id=task.task_id,
                        device_id=device_id,
                        step_number=task.current_step,
                        phase="observe",
                        stage="observe_error_user_decision",
                        message=task.to_observe_error_status_message() or pending_decision.message,
                        error=pending_decision.message,
                        error_type=pending_decision.error_type,
                        success=False,
                        data=pending_decision.to_payload(),
                        session_id=task.session_id or task.task_id,
                        run_id=task.run_id,
                    )

                if pending_decision is None:
                    scheduler_logger.info(
                        f"[CYCLE] observe error recovered via reflection: device={device_id}, "
                        f"task={task.task_id}, count={task.consecutive_observe_error_count}, "
                        f"max_retries={task.max_observe_error_retries}"
                    )
                    if not await self._guard_task_ownership(device_id, task, execution_token, "before_requeue_after_observe_error"):
                        return True
                    self.requeue_task(device_id, task, execution_token)
                    return False

                scheduler_logger.info(
                    f"[CYCLE] observe error waiting for user decision: device={device_id}, "
                    f"task={task.task_id}, count={pending_decision.consecutive_count}, "
                    f"max_retries={pending_decision.max_retries}"
                )
                return True

            await self._emit_failed_if_owned(task, e.message, e.error_type, execution_token)
            return True

        except Exception as e:
            scheduler_logger.error(
                f"[CYCLE] Unexpected exception: device={device_id}, task={task.task_id}, error={e}",
                exc_info=True,
            )
            await self._emit_failed_if_owned(task, str(e), ReActErrorType.OBSERVE_ERROR, execution_token)
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
        bootstrap_case = bool(task and task.is_active and step_number == 0 and task.current_step == 0)

        if bootstrap_case and task:
            task.initial_screenshot = screenshot or task.initial_screenshot
            task.initial_observation = observation or task.initial_observation
            task.initial_screenshot_path = screenshot_path or task.initial_screenshot_path
            task.initial_observe_success = success and not error
            task.last_active_at = time.time()
            self.consume_bootstrap_observe_result(
                task.task_id,
                device_id,
                task.initial_screenshot,
                task.initial_observation,
                success=success,
                error=error,
            )
            scheduler_logger.info(
                f"[OBSERVE] stored bootstrap result: device={device_id}, task={task.task_id}, "
                f"success={task.initial_observe_success}, has_screenshot={bool(task.initial_screenshot)}"
            )
            try:
                from src.services.file_storage import file_storage
                file_storage.append_react_record(device_id, {
                    "step_number": 0,
                    "screenshot": screenshot_path,
                    "observation": observation,
                    "success": task.initial_observe_success,
                    "error": error,
                    "phase": "bootstrap_observe",
                    "version": round_version,
                })
            except Exception as e:
                scheduler_logger.warning(f"[OBSERVE] Failed to save bootstrap observe record: {e}")
            return

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
        if screenshot_path:
            record.screenshot_path = screenshot_path
        if observation:
            record.observation = observation
        record.success = success and not error
        if record.step_number > 0:
            unchanged_result, compare_debug = _compare_base64_images_exact(
                record.before_action_screenshot,
                record.screenshot,
            )
            record.screenshot_unchanged = unchanged_result
            scheduler_logger.info(
                f"[OBSERVE COMPARE] device={device_id}, task={task.task_id}, step={record.step_number}, "
                f"has_before={compare_debug['has_before']}, has_after={compare_debug['has_after']}, "
                f"before_length={compare_debug['before_length']}, after_length={compare_debug['after_length']}, "
                f"before_hash={compare_debug['before_hash']}, after_hash={compare_debug['after_hash']}, "
                f"before_error={compare_debug['before_error']}, after_error={compare_debug['after_error']}, "
                f"screenshot_unchanged={record.screenshot_unchanged}"
            )
        if record.success:
            task.reset_observe_error_counter()
        task.last_active_at = time.time()

        scheduler_logger.info(
            f"[OBSERVE] stored result: device={device_id}, task={task.task_id}, "
            f"step={record.step_number}, version={round_version}, success={record.success}, "
            f"has_screenshot={bool(screenshot)}, screenshot_unchanged={record.screenshot_unchanged}, phase={task.phase.value}"
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

    async def resolve_observe_error_decision(self, device_id: str, decision: str, advice: str = "") -> bool:
        """处理 observe-error 超限后的用户决策。"""
        task = self._device_tasks.get(device_id)
        if not task or not task.is_active or not task.is_waiting_observe_error_decision():
            return False

        pending = task.resolve_observe_error_decision(decision, advice)
        if not pending:
            return False

        if decision == "interrupt":
            await self.interrupt_task(device_id)
            return True

        if self._ws_hub:
            self.broadcast_agent_progress(
                task_id=task.task_id,
                device_id=device_id,
                step_number=task.current_step,
                phase="observe",
                stage="observe_error_retry_resumed",
                message="用户选择继续任务，准备重新推理",
                error=pending.message,
                error_type=pending.error_type,
                success=False,
                data={
                    **pending.to_payload(),
                    "decision": decision,
                    "advice": advice or "",
                },
            )

        with self._queue_lock:
            if device_id not in self._task_queue and task.is_active:
                self._task_queue.append(device_id)
                self._last_queue_state = None

        scheduler_logger.info(
            f"[OBSERVE DECISION] resumed task after user continue: device={device_id}, "
            f"task={task.task_id}, advice_present={bool((advice or '').strip())}"
        )
        return True

    def confirm_phase(self, device_id: str, approved: bool):
        """Backward-compatible alias for legacy ws.py confirm_phase messages."""
        decision = "continue" if approved else "interrupt"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.create_task(self.resolve_observe_error_decision(device_id, decision))
            return

        scheduler_logger.warning(
            f"[CONFIRM_PHASE] no running event loop for device={device_id}, approved={approved}"
        )

    async def interrupt_task(self, device_id: str):
        """中断任务"""
        task = self._device_tasks.get(device_id)
        if not task:
            return

        task.pending_observe_error_decision = None
        task.status = TaskStatus.INTERRUPTED
        task.session_status = SessionStatus.FINISHED

        # Cancel pending action
        try:
            from src.services.action_router import action_router

            if action_router:
                await action_router.cancel_action(task.task_id, device_id)
        except Exception as e:
            scheduler_logger.warning(f"[INTERRUPT] cancel failed: device={device_id}, task={task.task_id}, error={e}")

        # Interrupt does NOT clear session context — only user-initiated "clear context" does

        # 发送中断事件 — 使用专用的 on_task_interrupted，避免 on_task_failed 错误广播 "failed"
        event = ReActTaskEvent(
            device_id=task.device_id,
            task_id=task.task_id,
            status="interrupted",
            message="Task interrupted by user",
            final_reasoning=None,
            error_type=None,
            session_id=getattr(task, "session_id", None),
            run_id=getattr(task, "run_id", None),
        )
        for cb in task.callbacks:
            try:
                # Prefer dedicated on_task_interrupted; fall back to on_task_failed for compatibility
                if hasattr(cb, "on_task_interrupted"):
                    await cb.on_task_interrupted(event)
                elif hasattr(cb, "on_task_failed"):
                    await cb.on_task_failed(event)
            except Exception as e:
                scheduler_logger.warning(f"[CALLBACK] interrupt callback error: {e}")

        # 直接通过 ws_hub 广播 interrupted，确保不被 on_task_failed 路径覆盖
        if self._ws_hub:
            session_id = getattr(task, "session_id", None) or task.task_id
            self._ws_hub.broadcast_agent_status(
                device_id=task.device_id,
                session_id=session_id,
                status="interrupted",
                message="Task interrupted by user",
                data={
                    "task_id": task.task_id,
                    "session_id": session_id,
                    "run_id": getattr(task, "run_id", None),
                },
            )

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
            self.broadcast_agent_status(
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
        task = self._device_tasks.get(device_id)
        session_id = task.session_id if task else ""
        run_id = getattr(task, "run_id", None) if task else None
        if self._ws_hub:
            self.broadcast_agent_step(
                task_id="",
                device_id=device_id,
                step=step.__dict__,
                step_type="agent_step",
                session_id=session_id,
                run_id=run_id,
            )

    async def _broadcast_complete(self, device_id: str, message: str):
        """广播任务完成（兼容旧接口）"""
        if self._ws_hub:
            self.broadcast_agent_status(
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
            acquired = self.get_next_task()
            if not acquired:
                time.sleep(0.1)
                continue

            task, execution_token = acquired
            device_id = task.device_id

            with self._running_lock:
                self._running_tasks[thread_id] = device_id

            if not self._set_task_running_if_owned(device_id, task, execution_token):
                scheduler_logger.info(
                    f"[WORKER_{worker_id}] lost ownership before run: device={device_id}, task={task.task_id}, token={execution_token}"
                )
                with self._running_lock:
                    self._running_tasks.pop(thread_id, None)
                self._release_execution_token(device_id, execution_token)
                continue

            print(f"[WORKER_{worker_id}] SET STATUS TO RUNNING for {device_id}, {task.task_id}", flush=True)
            sys.stdout.flush()
            try:
                scheduler_logger.info(
                    f"[WORKER_{worker_id}] task acquired: device={device_id}, task={task.task_id}, "
                    f"step={task.current_step}, phase={task.phase.value}, token={execution_token}, running={len(self._running_tasks)}"
                )
            except Exception as e:
                print(f"[ERROR] Logger failed: {e}", flush=True)

            loop = None
            try:
                print(f"[DEBUG] WORKER_{worker_id} creating event loop for {device_id}", flush=True)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                print(f"[DEBUG] WORKER_{worker_id} running cycle for {device_id}", flush=True)
                loop.run_until_complete(self.run_one_cycle(device_id, task, execution_token))
                print(f"[DEBUG] WORKER_{worker_id} cycle finished for {device_id}", flush=True)
                scheduler_logger.info(
                    f"[WORKER_{worker_id}] cycle completed: device={device_id}, task={task.task_id}, token={execution_token}"
                )
            except Exception as e:
                print(f"[ERROR] WORKER_{worker_id} Exception for {device_id}: {e}", flush=True)
                import traceback
                traceback.print_exc()
                scheduler_logger.error(f"[WORKER_{worker_id}] Error processing device={device_id}: {e}", exc_info=True)
            finally:
                if loop is not None:
                    loop.close()
                self._release_execution_token(device_id, execution_token)
                with self._running_lock:
                    self._running_tasks.pop(thread_id, None)
                    scheduler_logger.debug(f"[WORKER_{worker_id}] Removed from running_tasks, remaining: {list(self._running_tasks.values())}")

                pending_task = self.get_task(device_id)
                if (
                    pending_task
                    and pending_task.is_active
                    and pending_task.session_status != SessionStatus.WAIT_USER_DECISION
                    and device_id not in self._task_queue
                ):
                    scheduler_logger.info(
                        f"[WORKER_{worker_id}] requeueing active task after release: device={device_id}, task={pending_task.task_id}"
                    )
                    with self._queue_lock:
                        if device_id not in self._task_queue:
                            self._task_queue.append(device_id)
                            self._last_queue_state = None

            asyncio.set_event_loop(None)


# 全局单例
scheduler = ReActScheduler()
