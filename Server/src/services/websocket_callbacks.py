"""
WebSocket 回调实现 - 将 ReAct 事件通过 WebSocket 广播
"""
from typing import TYPE_CHECKING

from src.services.react_callbacks import (
    ReActCallback,
    ReActStepEvent,
    ReActTaskEvent,
)

if TYPE_CHECKING:
    from src.services.websocket import WebSocketHub


class WebSocketReActCallback:
    """通过 WebSocket 广播 ReAct 事件的回调实现"""

    def __init__(self, ws_hub: "WebSocketHub"):
        self._ws_hub = ws_hub

    async def on_step(self, event: ReActStepEvent) -> None:
        """广播单步完成"""
        await self._ws_hub.broadcast_agent_step(
            task_id=event.task_id,
            device_id=event.device_id,
            step={
                "step_number": event.step_number,
                "reasoning": event.reasoning,
                "action": event.action,
                "action_result": event.result,
                "screenshot": event.screenshot or "",
                "success": event.success,
                "error": event.error,
            },
            step_type="agent_step",
        )

    async def on_task_complete(self, event: ReActTaskEvent) -> None:
        """广播任务完成"""
        await self._ws_hub.broadcast_agent_status(
            device_id=event.device_id,
            session_id=event.task_id,
            status="completed",
            message=event.message,
            data={
                "task_id": event.task_id,
                "final_reasoning": event.final_reasoning,
            },
        )

    async def on_task_failed(self, event: ReActTaskEvent) -> None:
        """广播任务失败"""
        await self._ws_hub.broadcast_agent_status(
            device_id=event.device_id,
            session_id=event.task_id,
            status="failed",
            message=event.message,
            data={
                "task_id": event.task_id,
                "error_type": event.error_type,
                "final_reasoning": event.final_reasoning,
            },
        )

    async def on_phase_start(
        self,
        device_id: str,
        task_id: str,
        phase: str,
        step: int
    ) -> None:
        """广播阶段开始"""
        await self._ws_hub.broadcast_agent_phase_start(
            device_id=device_id,
            task_id=task_id,
            phase=phase,
            step_number=step,
        )


# 兼容旧的函数式接口
async def broadcast_step_via_hub(
    ws_hub: "WebSocketHub",
    device_id: str,
    task_id: str,
    step_number: int,
    reasoning: str,
    action: dict,
    result: str,
    screenshot: str = "",
    success: bool = True,
    error: str = "",
) -> None:
    """通过 WebSocketHub 广播单步的便捷函数"""
    await ws_hub.broadcast_agent_step(
        task_id=task_id,
        device_id=device_id,
        step={
            "step_number": step_number,
            "reasoning": reasoning,
            "action": action,
            "action_result": result,
            "screenshot": screenshot,
            "success": success,
            "error": error,
        },
        step_type="agent_step",
    )


async def broadcast_phase_start_via_hub(
    ws_hub: "WebSocketHub",
    device_id: str,
    task_id: str,
    phase: str,
    step: int,
) -> None:
    """通过 WebSocketHub 广播阶段开始的便捷函数"""
    await ws_hub.broadcast_agent_phase_start(
        device_id=device_id,
        task_id=task_id,
        phase=phase,
        step_number=step,
    )


async def broadcast_task_complete_via_hub(
    ws_hub: "WebSocketHub",
    device_id: str,
    task_id: str,
    message: str,
    final_reasoning: str = "",
) -> None:
    """通过 WebSocketHub 广播任务完成的便捷函数"""
    await ws_hub.broadcast_agent_status(
        device_id=device_id,
        session_id=task_id,
        status="completed",
        message=message,
        data={
            "task_id": task_id,
            "final_reasoning": final_reasoning,
        },
    )


async def broadcast_task_failed_via_hub(
    ws_hub: "WebSocketHub",
    device_id: str,
    task_id: str,
    message: str,
    error_type: str = "",
    final_reasoning: str = "",
) -> None:
    """通过 WebSocketHub 广播任务失败的便捷函数"""
    await ws_hub.broadcast_agent_status(
        device_id=device_id,
        session_id=task_id,
        status="failed",
        message=message,
        data={
            "task_id": task_id,
            "error_type": error_type,
            "final_reasoning": final_reasoning,
        },
    )
