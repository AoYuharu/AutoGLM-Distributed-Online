"""
Action Router - Sends action_cmd to clients and tracks pending actions

This module handles:
- Sending parsed actions (action_cmd) to clients via WebSocketHub
- Tracking pending actions with ACK/observe timeouts
- Handling action acknowledgments
- Handling observe_result idempotently by (device_id, version)
- Supporting interrupt/cancellation of pending actions
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

import structlog

from src.logging_config import get_network_logger, get_ws_console_logger

logger = structlog.get_logger()
network_logger = get_network_logger()
ws_console_logger = get_ws_console_logger()


class ActionStatus(Enum):
    """Status of a pending action"""

    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    COMPLETED = "completed"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class PendingAction:
    """A pending action awaiting ACK and observe result"""

    action_id: str
    sent_msg_id: str = ""
    task_id: str = ""
    device_id: str = ""
    round_version: int = 0
    action: dict = field(default_factory=dict)
    reasoning: str = ""
    step_number: int = 0
    status: ActionStatus = ActionStatus.PENDING
    created_at: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    timeout_seconds: float = 15.0
    observe_timeout_seconds: float = 30.0
    ack_received: bool = False
    observe_received: bool = False
    result: Optional[dict] = None
    error: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.last_update) > self.timeout_seconds

    @property
    def round_key(self) -> tuple[str, int]:
        return (self.device_id, self.round_version)


class ActionRouter:
    """Routes parsed actions to clients via WebSocketHub."""

    COMPLETED_ROUND_RETENTION_SECONDS = 300.0

    def __init__(self, ws_hub):
        self._ws_hub = ws_hub
        self._pending_actions: Dict[str, PendingAction] = {}
        self._pending_actions_by_msg_id: Dict[str, PendingAction] = {}
        self._pending_actions_by_round: Dict[tuple[str, int], PendingAction] = {}
        self._action_futures: Dict[str, asyncio.Future] = {}
        self._ack_futures: Dict[str, asyncio.Future] = {}
        self._completed_rounds: Dict[tuple[str, int], dict] = {}
        self._completed_results_by_action_id: Dict[str, dict] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("ActionRouter started")

    async def stop(self):
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("ActionRouter stopped")

    async def _send_to_device_with_fallback(self, device_id: str, message: dict) -> bool:
        if self._ws_hub.is_device_connected(device_id):
            success = await self._ws_hub.send_to_device(device_id, message)
            if success:
                network_logger.info(f"[_send_to_device] Sent directly to device_id={device_id}")
                return True

        client_id = await self._get_client_id_for_device(device_id)
        if client_id:
            connection_id = self._ws_hub._client_connections.get(client_id)
            if connection_id:
                success = await self._ws_hub.send_to_connection(connection_id, message)
                if success:
                    network_logger.info(
                        f"[_send_to_device] Sent via client_id={client_id} to device_id={device_id}"
                    )
                    return True
                network_logger.warning(
                    f"[_send_to_device] Failed to send via client_id={client_id}"
                )
            else:
                network_logger.warning(
                    f"[_send_to_device] client_id={client_id} not in _client_connections, "
                    f"keys={list(self._ws_hub._client_connections.keys())}"
                )
        else:
            network_logger.warning(f"[_send_to_device] No client_id found for device_id={device_id}")

        return False

    async def _get_client_id_for_device(self, device_id: str) -> Optional[str]:
        try:
            from sqlalchemy import select

            from src.database import get_db
            from src.models.models import Device

            db_gen = get_db()
            db = next(db_gen)
            device = db.execute(select(Device).where(Device.device_id == device_id)).scalar_one_or_none()
            if device and device.client_id:
                return str(device.client_id)
        except Exception as e:
            logger.error(f"[_get_client_id_for_device] Error looking up client_id: {e}")

        return None

    @staticmethod
    def _parse_round_version(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _build_result(
        self,
        pending: PendingAction,
        *,
        success: bool,
        result: str = "",
        screenshot: Optional[str] = None,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> dict:
        return {
            "success": success,
            "result": result,
            "screenshot": screenshot,
            "error": error,
            "error_type": error_type,
            "action_id": pending.action_id,
            "task_id": pending.task_id,
            "device_id": pending.device_id,
            "version": pending.round_version,
            "step_number": pending.step_number,
        }

    def _store_completed_round(self, pending: PendingAction, result: dict):
        completed = dict(result)
        completed["_stored_at"] = time.time()
        self._completed_rounds[pending.round_key] = completed

    def _cleanup_action(self, action_id: str):
        pending = self._pending_actions.pop(action_id, None)
        if pending:
            if pending.sent_msg_id:
                self._pending_actions_by_msg_id.pop(pending.sent_msg_id, None)
            self._pending_actions_by_round.pop(pending.round_key, None)
        self._action_futures.pop(action_id, None)
        self._ack_futures.pop(action_id, None)

    def _finalize_pending(self, pending: PendingAction, result: dict):
        pending.result = result
        self._store_completed_round(pending, result)
        self._completed_results_by_action_id[pending.action_id] = dict(result)

        ack_future = self._ack_futures.get(pending.action_id)
        if ack_future and not ack_future.done() and pending.ack_received:
            ack_future.set_result(
                {
                    "accepted": pending.status != ActionStatus.CANCELLED,
                    "device_id": pending.device_id,
                    "version": pending.round_version,
                    "error": pending.error,
                }
            )

        future = self._action_futures.get(pending.action_id)
        if future and not future.done():
            future.set_result(result)

        self._cleanup_action(pending.action_id)

    def _expire_action(self, action_id: str, error_type: str, message: str):
        pending = self._pending_actions.get(action_id)
        if not pending:
            return

        pending.status = ActionStatus.TIMEOUT
        pending.error = message
        pending.last_update = time.time()

        ack_future = self._ack_futures.get(action_id)
        if ack_future and not ack_future.done():
            ack_future.set_result(
                {
                    "accepted": False,
                    "device_id": pending.device_id,
                    "version": pending.round_version,
                    "error": message,
                    "error_type": error_type,
                }
            )

        result = self._build_result(
            pending,
            success=False,
            error=message,
            error_type=error_type,
        )
        self._finalize_pending(pending, result)

    async def send_action(
        self,
        task_id: str,
        device_id: str,
        action: dict,
        reasoning: str = "",
        step_number: int = 0,
        round_version: int = 0,
        ack_timeout_seconds: float = 15.0,
        observe_timeout_seconds: float = 30.0,
    ) -> PendingAction:
        from src.network.message_types import create_action_cmd

        action_id = str(uuid.uuid4())
        msg = create_action_cmd(
            task_id=task_id,
            device_id=device_id,
            step_number=step_number,
            action=action,
            reasoning=reasoning,
            version=str(round_version),
        )

        pending = PendingAction(
            action_id=action_id,
            sent_msg_id=msg.msg_id,
            task_id=task_id,
            device_id=device_id,
            round_version=round_version,
            action=action,
            reasoning=reasoning,
            step_number=step_number,
            timeout_seconds=ack_timeout_seconds,
            observe_timeout_seconds=observe_timeout_seconds,
        )

        self._pending_actions[action_id] = pending
        self._pending_actions_by_msg_id[msg.msg_id] = pending
        self._pending_actions_by_round[pending.round_key] = pending
        self._action_futures[action_id] = asyncio.get_event_loop().create_future()
        self._ack_futures[action_id] = asyncio.get_event_loop().create_future()

        success = await self._send_to_device_with_fallback(device_id, msg.to_dict())
        if not success:
            pending.status = ActionStatus.CANCELLED
            pending.error = "Device not connected"
            result = self._build_result(
                pending,
                success=False,
                error="Device not connected",
                error_type="send_failed",
            )
            self._finalize_pending(pending, result)
            return pending

        logger.info(
            "Action sent to client",
            action_id=action_id,
            task_id=task_id,
            device_id=device_id,
            version=round_version,
            action_type=action.get("action"),
            ack_timeout_seconds=ack_timeout_seconds,
            observe_timeout_seconds=observe_timeout_seconds,
        )
        network_logger.info(
            f"[action_cmd] Sent to device_id={device_id}, task_id={task_id}, "
            f"version={round_version}, action={action.get('action')}"
        )
        return pending

    async def wait_for_ack(self, action_id: str, timeout: Optional[float] = None) -> dict:
        future = self._ack_futures.get(action_id)
        if future is None:
            completed = self._completed_results_by_action_id.get(action_id)
            if completed is not None:
                return {
                    "accepted": completed.get("error_type") not in {"ack_timeout", "ack_rejected", "send_failed"},
                    "device_id": completed.get("device_id"),
                    "version": completed.get("version"),
                    "error": completed.get("error"),
                    "error_type": completed.get("error_type"),
                }
            return {"accepted": False, "error": f"Unknown action_id: {action_id}"}
        if timeout is None:
            return await future
        return await asyncio.wait_for(future, timeout=timeout)

    async def wait_for_result(self, action_id: str, timeout: Optional[float] = None) -> dict:
        future = self._action_futures.get(action_id)
        if future is None:
            completed = self._completed_results_by_action_id.get(action_id)
            if completed is not None:
                return completed
            return {"success": False, "error": f"Unknown action_id: {action_id}"}
        if timeout is None:
            return await future
        return await asyncio.wait_for(future, timeout=timeout)

    async def handle_observe_result(self, msg: dict) -> bool:
        task_id = msg.get("task_id")
        device_id = msg.get("device_id")
        step_number = msg.get("step_number")
        result = msg.get("result", "")
        success = msg.get("success", True)
        screenshot = msg.get("screenshot")
        error = msg.get("error")
        round_version = self._parse_round_version(msg.get("version") or msg.get("round_version"))

        pending: Optional[PendingAction] = None
        if device_id and round_version is not None:
            pending = self._pending_actions_by_round.get((device_id, round_version))
            if pending is None and (device_id, round_version) in self._completed_rounds:
                logger.info(
                    "Duplicate observe_result ignored for completed round",
                    device_id=device_id,
                    version=round_version,
                )
                return True

        if pending is None:
            pending = self._find_pending_action(task_id, step_number)

        if not pending:
            logger.warning(
                "Observe result with no pending action",
                task_id=task_id,
                device_id=device_id,
                step_number=step_number,
                version=round_version,
            )
            return False

        if pending.observe_received or pending.status in {
            ActionStatus.COMPLETED,
            ActionStatus.ERROR,
            ActionStatus.TIMEOUT,
            ActionStatus.CANCELLED,
        }:
            logger.info(
                "Duplicate observe_result ignored",
                action_id=pending.action_id,
                device_id=pending.device_id,
                version=pending.round_version,
            )
            return True

        pending.observe_received = True
        pending.last_update = time.time()
        pending.status = ActionStatus.COMPLETED if success else ActionStatus.ERROR
        if error:
            pending.error = error

        observe_result = self._build_result(
            pending,
            success=success,
            result=result,
            screenshot=screenshot,
            error=error,
            error_type=None if success else "observe_error",
        )

        logger.info(
            "Observe result received",
            action_id=pending.action_id,
            task_id=pending.task_id,
            device_id=pending.device_id,
            step_number=pending.step_number,
            version=pending.round_version,
            success=success,
        )

        await self._push_agent_step(
            task_id=pending.task_id,
            device_id=pending.device_id,
            step_number=pending.step_number,
            action=pending.action,
            reasoning=pending.reasoning,
            result=result,
            screenshot=screenshot,
            success=success,
            error=error,
        )

        self._finalize_pending(pending, observe_result)
        return True

    async def handle_ack(self, msg: dict) -> bool:
        payload = msg.get("payload") or {}
        ref_msg_id = msg.get("ref_msg_id") or msg.get("msg_id")
        device_id = payload.get("device_id") or msg.get("device_id")
        round_version = self._parse_round_version(msg.get("version") or payload.get("version"))
        accepted = payload.get("accepted", msg.get("accepted", True))
        error = payload.get("error") or msg.get("error")
        error_code = payload.get("error_code") or msg.get("error_code")

        logger.info(
            "ACK received from client",
            ref_msg_id=ref_msg_id,
            device_id=device_id,
            version=round_version,
            accepted=accepted,
            error_code=error_code,
        )

        pending = self._pending_actions_by_msg_id.get(ref_msg_id)
        if pending is None and device_id and round_version is not None:
            pending = self._pending_actions_by_round.get((device_id, round_version))
            if pending is None and (device_id, round_version) in self._completed_rounds:
                logger.info(
                    "Duplicate ACK ignored for completed round",
                    device_id=device_id,
                    version=round_version,
                )
                return True

        if pending is None:
            logger.debug("ACK for unknown action or already completed", ref_msg_id=ref_msg_id)
            return True

        if pending.ack_received:
            logger.info(
                "Duplicate ACK ignored",
                action_id=pending.action_id,
                device_id=pending.device_id,
                version=pending.round_version,
            )
            return True

        pending.ack_received = True
        pending.last_update = time.time()

        ack_future = self._ack_futures.get(pending.action_id)
        ack_result = {
            "accepted": accepted,
            "device_id": pending.device_id,
            "version": pending.round_version,
            "error": error,
            "error_code": error_code,
        }

        if accepted:
            pending.status = ActionStatus.ACKNOWLEDGED
            pending.timeout_seconds = pending.observe_timeout_seconds
            if ack_future and not ack_future.done():
                ack_future.set_result(ack_result)
            logger.debug(
                "Pending action acknowledged",
                action_id=pending.action_id,
                sent_msg_id=pending.sent_msg_id,
                task_id=pending.task_id,
                version=pending.round_version,
            )
            return True

        pending.status = ActionStatus.CANCELLED
        pending.error = error or "Action rejected by client"
        if ack_future and not ack_future.done():
            ack_future.set_result(ack_result)

        result = self._build_result(
            pending,
            success=False,
            error=pending.error,
            error_type="ack_rejected",
        )
        self._finalize_pending(pending, result)
        return True

    async def cancel_action(self, task_id: str, device_id: str) -> bool:
        for action_id, pending in list(self._pending_actions.items()):
            if pending.task_id == task_id and pending.device_id == device_id:
                if pending.status in {ActionStatus.PENDING, ActionStatus.ACKNOWLEDGED}:
                    pending.status = ActionStatus.CANCELLED
                    pending.error = "Action cancelled"
                    pending.last_update = time.time()
                    result = self._build_result(
                        pending,
                        success=False,
                        error="Action cancelled",
                        error_type="cancelled",
                    )
                    self._finalize_pending(pending, result)
                    logger.info(
                        "Action cancelled",
                        action_id=action_id,
                        task_id=task_id,
                        device_id=device_id,
                    )
                    return True
        return False

    def _find_pending_action(self, task_id: str, step_number: int) -> Optional[PendingAction]:
        for pending in self._pending_actions.values():
            if pending.task_id == task_id and pending.step_number == step_number:
                if pending.status in {ActionStatus.PENDING, ActionStatus.ACKNOWLEDGED}:
                    return pending
        return None

    async def _cleanup_loop(self):
        while self._running:
            await asyncio.sleep(5)

            expired_action_ids = []
            now = time.time()
            for action_id, pending in list(self._pending_actions.items()):
                if pending.is_expired and pending.status in {ActionStatus.PENDING, ActionStatus.ACKNOWLEDGED}:
                    expired_action_ids.append((action_id, pending))

            for action_id, pending in expired_action_ids:
                if pending.ack_received:
                    self._expire_action(action_id, "observe_timeout", "Observation timeout")
                    logger.warning(
                        "Action observe timeout",
                        action_id=action_id,
                        task_id=pending.task_id,
                        device_id=pending.device_id,
                        version=pending.round_version,
                        age_seconds=now - pending.created_at,
                    )
                else:
                    self._expire_action(action_id, "ack_timeout", "Action ACK timeout")
                    logger.warning(
                        "Action ACK timeout",
                        action_id=action_id,
                        task_id=pending.task_id,
                        device_id=pending.device_id,
                        version=pending.round_version,
                        age_seconds=now - pending.created_at,
                    )

            for round_key, result in list(self._completed_rounds.items()):
                stored_at = result.get("_stored_at", 0.0)
                if (now - stored_at) > self.COMPLETED_ROUND_RETENTION_SECONDS:
                    self._completed_rounds.pop(round_key, None)

    def get_pending_count(self) -> int:
        return len(self._pending_actions)

    async def execute_action(
        self,
        task_id: str,
        device_id: str,
        action: dict,
        reasoning: str = "",
        step_number: int = 0,
        round_version: int = 0,
        ack_timeout_seconds: float = 15.0,
        observe_timeout_seconds: float = 30.0,
    ) -> dict:
        pending = await self.send_action(
            task_id=task_id,
            device_id=device_id,
            action=action,
            reasoning=reasoning,
            step_number=step_number,
            round_version=round_version,
            ack_timeout_seconds=ack_timeout_seconds,
            observe_timeout_seconds=observe_timeout_seconds,
        )

        if pending.status == ActionStatus.CANCELLED and pending.error == "Device not connected":
            return pending.result or self._build_result(
                pending,
                success=False,
                error="Device not connected",
                error_type="send_failed",
            )

        try:
            ack = await self.wait_for_ack(pending.action_id, timeout=ack_timeout_seconds)
        except asyncio.TimeoutError:
            self._expire_action(pending.action_id, "ack_timeout", "Action ACK timeout")
            return {
                "success": False,
                "error": "Action ACK timeout",
                "error_type": "ack_timeout",
                "action_id": pending.action_id,
                "task_id": task_id,
                "device_id": device_id,
                "version": round_version,
                "step_number": step_number,
            }

        if not ack.get("accepted", False):
            return {
                "success": False,
                "error": ack.get("error") or "Action rejected by client",
                "error_type": "ack_rejected",
                "action_id": pending.action_id,
                "task_id": task_id,
                "device_id": device_id,
                "version": round_version,
                "step_number": step_number,
            }

        try:
            result = await self.wait_for_result(pending.action_id, timeout=observe_timeout_seconds)
        except asyncio.TimeoutError:
            self._expire_action(pending.action_id, "observe_timeout", "Observation timeout")
            return {
                "success": False,
                "error": "Observation timeout",
                "error_type": "observe_timeout",
                "action_id": pending.action_id,
                "task_id": task_id,
                "device_id": device_id,
                "version": round_version,
                "step_number": step_number,
            }

        return result

    async def _push_agent_step(
        self,
        task_id: str,
        device_id: str,
        step_number: int,
        action: dict,
        reasoning: str,
        result: str,
        screenshot: Optional[str],
        success: bool,
        error: Optional[str] = None,
    ):
        from src.services.websocket import ws_hub

        message = {
            "type": "agent_step",
            "task_id": task_id,
            "device_id": device_id,
            "step_number": step_number,
            "action": action,
            "reasoning": reasoning,
            "result": result,
            "screenshot": screenshot,
            "success": success,
        }

        if error:
            message["error"] = error

        await ws_hub.broadcast_to_web_consoles(message)
        ws_console_logger.info(
            f"[agent_step] Broadcast to all Web Consoles: device_id={device_id}, step={step_number}, success={success}"
        )

    async def push_agent_status(
        self,
        device_id: str,
        task_id: str,
        status: str,
        message: str,
        data: dict = None,
    ):
        from src.services.websocket import ws_hub

        ws_message = {
            "type": "agent_status",
            "device_id": device_id,
            "task_id": task_id,
            "status": status,
            "message": message,
        }

        if data:
            ws_message["data"] = data

        await ws_hub.broadcast_to_web_consoles(ws_message)
        ws_console_logger.info(
            f"[agent_status] Broadcast to all Web Consoles: device_id={device_id}, status={status}"
        )

    async def push_session_locked(
        self,
        device_id: str,
        controller_id: str,
    ):
        from src.services.websocket import ws_hub

        message = {
            "type": "session_locked",
            "device_id": device_id,
            "controller_id": controller_id,
        }

        await ws_hub.broadcast_to_web_consoles(message, subscribed_only=False)
        ws_console_logger.info(
            f"[session_locked] Broadcast to all Web Consoles: device_id={device_id}, controller_id={controller_id}"
        )

    async def push_session_released(
        self,
        device_id: str,
    ):
        from src.services.websocket import ws_hub

        message = {
            "type": "session_released",
            "device_id": device_id,
        }

        await ws_hub.broadcast_to_web_consoles(message, subscribed_only=False)
        ws_console_logger.info(
            f"[session_released] Broadcast to all Web Consoles: device_id={device_id}"
        )

    async def push_action_pending(
        self,
        device_id: str,
        task_id: str,
        step_number: int,
        action: dict,
        reasoning: str,
    ):
        from src.services.websocket import ws_hub

        message = {
            "type": "agent_action_pending",
            "device_id": device_id,
            "task_id": task_id,
            "step_number": step_number,
            "action": action,
            "reasoning": reasoning,
        }

        await ws_hub.broadcast_to_web_consoles(message)
        ws_console_logger.info(
            f"[agent_action_pending] Broadcast to all Web Consoles: device_id={device_id}, step={step_number}"
        )


action_router = ActionRouter(ws_hub=None)  # type: ignore
