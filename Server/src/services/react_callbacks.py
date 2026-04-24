"""
ReAct 回调接口定义
"""
from dataclasses import dataclass
from typing import Protocol, Optional, Any


@dataclass
class ReActStepEvent:
    """ReAct 单步事件"""
    device_id: str
    task_id: str
    step_number: int
    phase: str  # "reason" | "act" | "observe"
    reasoning: str
    action: dict
    result: str
    screenshot: Optional[str]
    success: bool
    error: Optional[str]
    error_type: Optional[str]  # ReActErrorType value


@dataclass
class ReActTaskEvent:
    """ReAct 任务事件"""
    device_id: str
    task_id: str
    status: str  # "completed" | "failed" | "interrupted"
    message: str
    final_reasoning: Optional[str]
    error_type: Optional[str] = None  # ReActErrorType value
    session_id: Optional[str] = None  # 持久会话ID（task_id的别名/升级）
    run_id: Optional[str] = None  # 当前运行ID（每次自动运行新建）


class ReActCallback(Protocol):
    """ReAct 回调协议"""

    async def on_step(self, event: ReActStepEvent) -> None:
        """单步完成回调"""
        ...

    async def on_task_complete(self, event: ReActTaskEvent) -> None:
        """任务完成回调"""
        ...

    async def on_task_failed(self, event: ReActTaskEvent) -> None:
        """任务失败回调"""
        ...

    async def on_task_interrupted(self, event: ReActTaskEvent) -> None:
        """任务中断回调（用户主动中断或设备断开）"""
        ...

    async def on_phase_start(
        self,
        device_id: str,
        task_id: str,
        phase: str,
        step: int
    ) -> None:
        """阶段开始回调"""
        ...
