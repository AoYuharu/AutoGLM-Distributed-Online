"""
Simplified WebSocket service for Server <-> Client communication

Only handles:
- Device registration
- Direct device-to-server message routing
- Action command delivery
"""

import asyncio
import uuid
from datetime import datetime
from typing import Dict, Optional
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect
import structlog

from src.config import settings
from src.services.file_storage import file_storage


def _coerce_message_text(*candidates) -> str:
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, str):
            if candidate:
                return candidate
            continue
        return str(candidate)
    return ""


def _build_progress_chat_message(device_id: str, ws_message: dict) -> dict:
    action = ws_message.get("action")
    action_type = None
    action_params = None
    if isinstance(action, dict):
        action_type = action.get("action") or action.get("type")
        action_params = action

    progress_message = _coerce_message_text(
        ws_message.get("progress_message"),
        ws_message.get("message"),
    )
    content = _coerce_message_text(
        ws_message.get("content"),
        progress_message,
        ws_message.get("result"),
        ws_message.get("error"),
    )

    return {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "role": "agent",
        "content": content,
        "created_at": ws_message.get("timestamp") or datetime.utcnow().isoformat(),
        "task_id": ws_message.get("task_id"),
        "step_number": ws_message.get("step_number"),
        "phase": ws_message.get("phase"),
        "stage": ws_message.get("stage"),
        "progress_status_text": ws_message.get("stage") or ws_message.get("status"),
        "progress_message": progress_message,
        "thinking": ws_message.get("reasoning") or ws_message.get("thinking"),
        "action_type": action_type,
        "action_params": action_params,
        "screenshot_path": ws_message.get("screenshot") or ws_message.get("screenshot_path"),
        "result": _coerce_message_text(ws_message.get("result"), None),
        "success": ws_message.get("success"),
        "error": ws_message.get("error"),
        "error_type": ws_message.get("error_type"),
        "version": ws_message.get("version"),
        "error_code": ws_message.get("error_code"),
        "data": ws_message.get("data"),
        "source": "canonical_websocket",
        "message_type": ws_message.get("type"),
        "device_id": device_id,
    }


def _persist_chat_history_message(device_id: str, ws_message: dict) -> None:
    try:
        file_storage.append_chat_message(device_id, _build_progress_chat_message(device_id, ws_message))
    except Exception as e:
        logger.warning(
            "[chat_history_persist] Failed to append chat history entry",
            device_id=device_id,
            message_type=ws_message.get("type"),
            stage=ws_message.get("stage"),
            error=str(e),
        )


def _should_persist_progress_stage(stage: Optional[str]) -> bool:
    return stage in {
        "reason_start",
        "reason_complete",
        "action_dispatched",
        "waiting_ack",
        "ack_received",
        "waiting_observe",
        "observe_received",
        "ack_timeout",
        "observe_timeout",
        "ack_rejected",
        "requesting_initial_screenshot",
        "initial_screenshot_ack_received",
        "initial_screenshot_received",
    }


def is_reason_detail_stage(stage: Optional[str]) -> bool:
    """Stages that should carry reasoning/action in the persisted chat history entry."""
    return stage in {"reason_start", "reason_stream", "reason_complete", "action_dispatched"}


def _should_persist_status(status: Optional[str]) -> bool:
    return status in {"completed", "failed"}


def _infer_legacy_phase(stage: str) -> str:
    canonical_stage_phases = {
        "reason_start": "reason",
        "reason_complete": "reason",
        "action_dispatched": "act",
        "waiting_ack": "act",
        "ack_received": "act",
        "ack_rejected": "act",
        "ack_timeout": "act",
        "waiting_observe": "observe",
        "observe_received": "observe",
        "observe_timeout": "observe",
        "requesting_initial_screenshot": "observe",
        "initial_screenshot_ack_received": "observe",
        "initial_screenshot_received": "observe",
        "initial_screenshot_failed": "observe",
    }
    return canonical_stage_phases.get(stage, "observe" if "observe" in stage else "act")



def _task_update_to_canonical_message(task_id: str, device_id: str, update: dict) -> Optional[dict]:
    if not update:
        return None

    stage = update.get("stage")
    status = update.get("status")

    if stage:
        message = {
            "type": "agent_progress",
            "task_id": task_id,
            "device_id": device_id,
            "step_number": update.get("step_number", 0),
            "phase": update.get("phase") or _infer_legacy_phase(stage),
            "stage": stage,
            "message": update.get("message") or update.get("progress_message") or stage,
            "timestamp": datetime.utcnow().isoformat(),
        }
        for key in (
            "version",
            "reasoning",
            "thinking",
            "action",
            "result",
            "success",
            "error",
            "error_type",
            "error_code",
            "screenshot",
            "screenshot_path",
            "data",
        ):
            value = update.get(key)
            if value is not None:
                message[key] = value
        return message

    if status:
        return {
            "type": "agent_status",
            "task_id": task_id,
            "device_id": device_id,
            "status": status,
            "message": update.get("message") or status,
            "data": update.get("data") or {},
            "timestamp": datetime.utcnow().isoformat(),
        }

    return None


logger = structlog.get_logger()


@dataclass
class ConnectionState:
    """WebSocket connection state"""
    connection_id: str
    client_id: Optional[str] = None
    device_id: Optional[str] = None  # Registered device ID
    connected_at: datetime = field(default_factory=datetime.utcnow)
    is_authenticated: bool = False
    capabilities: dict = field(default_factory=dict)  # Device capabilities


@dataclass
class ConsoleState:
    """Web Console connection state"""
    console_id: str
    subscribed_devices: set = field(default_factory=set)  # Set of device_ids this console subscribes to
    connected_at: datetime = field(default_factory=datetime.utcnow)


class WebSocketHub:
    """Simplified WebSocket connection manager for Server <-> Client"""

    def __init__(self):
        # Active connections (Client <-> Server)
        self.connections: Dict[str, WebSocket] = {}
        self.connection_states: Dict[str, ConnectionState] = {}

        # Device to connection mapping: device_id -> connection_id
        self._device_connections: Dict[str, str] = {}

        # Client ID to connection mapping: client_id -> connection_id
        # Used when client connects with client_id as the device_id query parameter
        self._client_connections: Dict[str, str] = {}

        # Web Console connections (Server -> Web)
        self._web_consoles: Dict[str, WebSocket] = {}
        self._console_states: Dict[str, ConsoleState] = {}
        self._console_subscriptions: Dict[str, set] = {}  # device_id -> set of console_ids

    async def start(self):
        """Start the WebSocket hub"""
        logger.info("WebSocket hub started (simplified)")

    async def stop(self):
        """Stop the WebSocket hub"""
        logger.info("WebSocket hub stopped")

    async def connect(self, websocket: WebSocket) -> str:
        """Accept a new WebSocket connection"""
        await websocket.accept()

        connection_id = str(uuid.uuid4())
        state = ConnectionState(connection_id=connection_id)

        self.connections[connection_id] = websocket
        self.connection_states[connection_id] = state

        logger.info("[ws_connect] WebSocket connected", connection_id=connection_id, total_connections=len(self.connections))
        return connection_id

    async def disconnect(self, connection_id: str, reason: str = "unknown"):
        """Disconnect a WebSocket connection"""
        if connection_id not in self.connections:
            return

        # Get state before removal
        state = self.connection_states.get(connection_id)
        device_id = state.device_id if state else None
        client_id = state.client_id if state else None

        # Unregister device if registered
        if device_id and device_id in self._device_connections:
            self.unregister_device(device_id)

        # Unregister client if registered
        if client_id and client_id in self._client_connections:
            del self._client_connections[client_id]

        # Remove connection
        del self.connections[connection_id]
        del self.connection_states[connection_id]

        logger.info("[ws_disconnect] WebSocket disconnected", connection_id=connection_id, reason=reason, total_connections=len(self.connections))

    async def send_message(self, connection_id: str, message: dict) -> bool:
        """Send a message to a specific connection"""
        return await self.send_to_connection(connection_id, message)

    async def send_to_connection(self, connection_id: str, message: dict) -> bool:
        """Send a message to a specific connection by connection_id"""
        if connection_id not in self.connections:
            return False

        msg_type = message.get("type", "unknown")
        try:
            await self.connections[connection_id].send_json(message)
            logger.debug(f"[ws_send] Message sent to {connection_id}: type={msg_type}")
            return True
        except Exception as e:
            logger.error(f"[ws_send] Failed to send message to {connection_id}: {e}", error=str(e), msg_type=msg_type)
            await self.disconnect(connection_id, reason=f"send_error: {e}")
            return False

    async def send_to_device(self, device_id: str, message: dict) -> bool:
        """
        Send a message to a specific device by device_id

        Args:
            device_id: The device ID to send to
            message: The message to send

        Returns:
            True if message was sent, False if device not connected
        """
        connection_id = self._device_connections.get(device_id)
        msg_type = message.get("type", "unknown")

        if not connection_id:
            logger.warning(f"[ws_send_to_device] Device {device_id} not connected: type={msg_type}",
                device_id=device_id,
                msg_type=msg_type,
            )
            return False

        try:
            await self.connections[connection_id].send_json(message)
            logger.info(f"[ws_send_to_device] Message sent to device {device_id}: type={msg_type}, conn={connection_id}")
            return True
        except Exception as e:
            logger.error(f"[ws_send_to_device] Failed to send to device {device_id}: {e}", error=str(e), device_id=device_id, msg_type=msg_type)
            await self.disconnect(connection_id, reason=f"send_error: {e}")
            return False

    def register_device(self, connection_id: str, device_id: str = None, client_id: str = None, capabilities: dict = None):
        """
        Register a device's WebSocket connection

        Args:
            connection_id: The WebSocket connection ID
            device_id: The device ID to register (optional, can be None for client-only connections)
            client_id: The client ID that owns the device (optional)
            capabilities: Optional device capabilities
        """
        # Update connection state
        if connection_id in self.connection_states:
            self.connection_states[connection_id].device_id = device_id
            self.connection_states[connection_id].client_id = client_id
            if capabilities:
                self.connection_states[connection_id].capabilities = capabilities

        # If device_id is provided, register it
        if device_id:
            # Remove old registration if exists
            old_connection_id = self._device_connections.get(device_id)
            if old_connection_id and old_connection_id != connection_id:
                # Device reconnected, update registration
                old_state = self.connection_states.get(old_connection_id)
                if old_state:
                    old_state.device_id = None

            self._device_connections[device_id] = connection_id

        # If client_id is provided, register it
        if client_id:
            # Remove old registration if exists
            old_connection_id = self._client_connections.get(client_id)
            if old_connection_id and old_connection_id != connection_id:
                # Client reconnected, update registration
                old_state = self.connection_states.get(old_connection_id)
                if old_state:
                    old_state.client_id = None

            self._client_connections[client_id] = connection_id

        logger.info("Device registered",
            device_id=device_id,
            client_id=client_id,
            connection_id=connection_id,
            capabilities=capabilities,
        )

        # Also update device_status_manager to set device as idle
        if device_id:
            from src.services.device_status_manager import device_status_manager
            # Schedule async update to avoid blocking
            asyncio.create_task(device_status_manager.set_idle(device_id))

    def unregister_device(self, device_id: str):
        """
        Unregister a device

        Args:
            device_id: The device ID to unregister
        """
        if device_id in self._device_connections:
            connection_id = self._device_connections[device_id]

            # Update connection state
            if connection_id in self.connection_states:
                self.connection_states[connection_id].device_id = None

            # Remove mapping
            del self._device_connections[device_id]

            logger.info("Device unregistered", device_id=device_id)

    def get_device_connection(self, device_id: str) -> Optional[str]:
        """Get connection ID for a device"""
        return self._device_connections.get(device_id)

    def is_device_connected(self, device_id: str) -> bool:
        """Check if a device is connected"""
        return device_id in self._device_connections

    # === Broadcast stub methods (no-op for backward compatibility) ===
    # These are kept as stubs since other modules depend on them
    # but they are not part of the simplified 4-message protocol

    async def broadcast_device_update(self, device_id: str, update: dict):
        """Broadcast device status update to all Web Consoles."""
        message = {
            "type": "device_status_update",
            "device_id": device_id,
            "status": update.get("status", "unknown"),
            "current_task_id": update.get("current_task_id"),
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self.broadcast_to_web_consoles(message)
        logger.info(f"[broadcast_device_update] Broadcast to all consoles: device={device_id}, status={update.get('status')}")

    async def broadcast_task_update(self, task_id: str, device_id: str, update: dict):
        """Compatibility wrapper that normalizes legacy task updates into canonical broadcasts."""
        canonical_message = _task_update_to_canonical_message(task_id, device_id, update)
        if canonical_message is None:
            logger.debug(
                "[broadcast_task_update] Ignored legacy update with no canonical mapping",
                task_id=task_id,
                device_id=device_id,
                update_keys=sorted(update.keys()) if isinstance(update, dict) else [],
            )
            return

        if canonical_message["type"] == "agent_progress":
            await self.broadcast_agent_progress(
                task_id=canonical_message["task_id"],
                device_id=canonical_message["device_id"],
                step_number=canonical_message.get("step_number", 0),
                phase=canonical_message.get("phase", "act"),
                stage=canonical_message.get("stage", "unknown"),
                message=canonical_message.get("message", ""),
                version=canonical_message.get("version"),
                **{
                    key: canonical_message[key]
                    for key in (
                        "reasoning",
                        "thinking",
                        "action",
                        "result",
                        "success",
                        "error",
                        "error_type",
                        "error_code",
                        "screenshot",
                        "screenshot_path",
                        "data",
                    )
                    if key in canonical_message
                },
            )
            return

        await self.broadcast_agent_status(
            device_id=device_id,
            session_id=task_id,
            status=canonical_message.get("status", ""),
            message=canonical_message.get("message", ""),
            data=canonical_message.get("data") or {"task_id": task_id},
        )

    async def broadcast_agent_step(self, task_id: str, device_id: str, step: dict, step_type: str = "agent_step"):
        """Broadcast agent step to all Web Consoles (broadcast mode, no subscription required)."""
        result = step.get("result")
        if result is None:
            result = step.get("action_result", "")

        message = {
            "type": "agent_step",
            "task_id": task_id,
            "device_id": device_id,
            "step_number": step.get("step_number", 0),
            "reasoning": step.get("reasoning", ""),
            "action": step.get("action", {}),
            "result": result,
            "screenshot": step.get("screenshot", ""),
            "success": step.get("success", True),
            "error": step.get("error"),
            "error_type": step.get("error_type"),
        }
        await self.broadcast_to_web_consoles(message)
        logger.info(f"[broadcast_agent_step] Broadcast to all consoles: device={device_id}, task={task_id}, step={step.get('step_number')}")

    async def broadcast_agent_progress(
        self,
        task_id: str,
        device_id: str,
        step_number: int,
        phase: str,
        stage: str,
        message: str,
        version: Optional[int] = None,
        **data,
    ):
        """Broadcast fine-grained agent progress updates to all Web Consoles."""
        ws_message: dict = {
            "type": "agent_progress",
            "task_id": task_id,
            "device_id": device_id,
            "step_number": step_number,
            "phase": phase,
            "stage": stage,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        }
        if version is not None:
            ws_message["version"] = version
        for key, value in data.items():
            if value is not None:
                ws_message[key] = value

        if _should_persist_progress_stage(stage):
            # Strip reasoning/action from transport milestone persisted entries to avoid
            # duplicate display in Web UI chat history.
            if not is_reason_detail_stage(stage):
                persist_message = {k: v for k, v in ws_message.items() if k not in ("reasoning", "action")}
            else:
                persist_message = ws_message
            _persist_chat_history_message(device_id, persist_message)

        await self.broadcast_to_web_consoles(ws_message)
        logger.info(
            f"[broadcast_agent_progress] Broadcast to all consoles: "
            f"device={device_id}, task={task_id}, step={step_number}, stage={stage}"
        )
        return ws_message


    async def broadcast_agent_phase_start(self, device_id: str, task_id: str, phase: str, step_number: int):
        """Broadcast agent phase start (reason/act/observe) to all Web Consoles."""
        message = {
            "type": "agent_phase_start",
            "task_id": task_id,
            "device_id": device_id,
            "phase": phase,
            "step_number": step_number,
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self.broadcast_to_web_consoles(message)
        logger.info(f"[broadcast_agent_phase_start] Broadcast to all consoles: device={device_id}, phase={phase}, step={step_number}")

    async def broadcast_agent_phase_end(self, device_id: str, task_id: str, phase: str, step_number: int,
                                        reasoning: str = "", action: dict = None, result: str = ""):
        """Broadcast agent phase end with results to all Web Consoles."""
        message = {
            "type": "agent_phase_end",
            "task_id": task_id,
            "device_id": device_id,
            "phase": phase,
            "step_number": step_number,
            "reasoning": reasoning,
            "action": action or {},
            "result": result,
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self.broadcast_to_web_consoles(message)
        logger.info(f"[broadcast_agent_phase_end] Broadcast to all consoles: device={device_id}, phase={phase}, step={step_number}")

    async def broadcast_agent_thinking(self, device_id: str, task_id: str, thinking: str, phase: str = "reason"):
        """Broadcast agent thinking update (streaming) to all Web Consoles."""
        message = {
            "type": "agent_thinking",
            "task_id": task_id,
            "device_id": device_id,
            "thinking": thinking,
            "phase": phase,
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self.broadcast_to_web_consoles(message)
        logger.debug(f"[broadcast_agent_thinking] Broadcast to all consoles: device={device_id}, phase={phase}")

    async def broadcast_agent_action_pending(self, device_id: str, task_id: str, step_number: int,
                                             action: dict, reasoning: str = ""):
        """Broadcast action pending confirmation (cautious mode) to all Web Consoles."""
        message = {
            "type": "agent_action_pending",
            "task_id": task_id,
            "device_id": device_id,
            "step_number": step_number,
            "action": action,
            "reasoning": reasoning,
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self.broadcast_to_web_consoles(message)
        logger.info(f"[broadcast_agent_action_pending] Broadcast to all consoles: device={device_id}, step={step_number}")

    async def broadcast_agent_status(self, device_id: str, session_id: str = "", status: str = "", message: str = "", data: dict = None):
        """Broadcast agent status update to all Web Consoles."""
        if data is None:
            data = {}
        ws_message = {
            "type": "agent_status",
            "device_id": device_id,
            "status": status,
            "message": message,
            "task_id": data.get("task_id", session_id or ""),
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        if _should_persist_status(status):
            _persist_chat_history_message(device_id, ws_message)
        await self.broadcast_to_web_consoles(ws_message)
        logger.info(f"[broadcast_agent_status] Broadcast to all consoles: device={device_id}, status={status}")
        return ws_message

    # === Web Console (Server -> Web) Methods ===

    async def register_web_console(self, websocket: WebSocket, console_id: str) -> bool:
        """
        Register a Web Console connection.

        Args:
            websocket: The WebSocket connection
            console_id: Unique console identifier

        Returns:
            True if registered successfully
        """
        await websocket.accept()

        state = ConsoleState(console_id=console_id)
        self._web_consoles[console_id] = websocket
        self._console_states[console_id] = state

        logger.info("[ws_console_register] Web Console registered",
            console_id=console_id,
            total_consoles=len(self._web_consoles),
        )
        return True

    async def unregister_web_console(self, console_id: str):
        """
        Unregister a Web Console connection.

        Args:
            console_id: The console ID to unregister
        """
        if console_id not in self._web_consoles:
            return

        # Remove from all device subscriptions
        state = self._console_states.get(console_id)
        if state:
            for device_id in state.subscribed_devices:
                if device_id in self._console_subscriptions:
                    self._console_subscriptions[device_id].discard(console_id)

        # Remove console
        del self._web_consoles[console_id]
        del self._console_states[console_id]

        logger.info("[ws_console_unregister] Web Console unregistered",
            console_id=console_id,
            remaining_consoles=len(self._web_consoles),
        )

    def subscribe_console_to_device(self, console_id: str, device_id: str):
        """
        Subscribe a Web Console to a device's updates.

        Args:
            console_id: The console ID
            device_id: The device ID to subscribe to
        """
        if console_id not in self._console_states:
            return

        self._console_states[console_id].subscribed_devices.add(device_id)

        if device_id not in self._console_subscriptions:
            self._console_subscriptions[device_id] = set()
        self._console_subscriptions[device_id].add(console_id)

        logger.debug("[ws_console_subscribe] Console subscribed to device",
            console_id=console_id,
            device_id=device_id,
        )

    def unsubscribe_console_from_device(self, console_id: str, device_id: str):
        """
        Unsubscribe a Web Console from a device's updates.

        Args:
            console_id: The console ID
            device_id: The device ID to unsubscribe from
        """
        if console_id in self._console_states:
            self._console_states[console_id].subscribed_devices.discard(device_id)

        if device_id in self._console_subscriptions:
            self._console_subscriptions[device_id].discard(console_id)

        logger.debug("[ws_console_unsubscribe] Console unsubscribed from device",
            console_id=console_id,
            device_id=device_id,
        )

    async def send_to_web(self, console_id: str, message: dict) -> bool:
        """
        Send a message to a specific Web Console.

        Args:
            console_id: The console ID
            message: The message to send

        Returns:
            True if sent successfully
        """
        if console_id not in self._web_consoles:
            return False

        msg_type = message.get("type", "unknown")
        try:
            await self._web_consoles[console_id].send_json(message)
            logger.debug(f"[ws_send_to_web] Message sent to console {console_id}: type={msg_type}")
            return True
        except Exception as e:
            logger.error(f"[ws_send_to_web] Failed to send to console {console_id}: {e}",
                error=str(e),
                msg_type=msg_type,
            )
            await self.unregister_web_console(console_id)
            return False

    async def send_to_web_device(self, device_id: str, message: dict) -> bool:
        """
        Send a message to all Web Consoles subscribed to a specific device.

        Args:
            device_id: The device ID
            message: The message to send

        Returns:
            True if at least one console received the message
        """
        console_ids = self._console_subscriptions.get(device_id, set())
        if not console_ids:
            logger.debug(f"[ws_send_to_web_device] No consoles subscribed to device {device_id}")
            return False

        msg_type = message.get("type", "unknown")
        sent_count = 0
        for console_id in list(console_ids):
            if console_id in self._web_consoles:
                try:
                    await self._web_consoles[console_id].send_json(message)
                    sent_count += 1
                except Exception as e:
                    logger.error(f"[ws_send_to_web_device] Failed to send to console {console_id}: {e}")
                    await self.unregister_web_console(console_id)

        logger.debug(f"[ws_send_to_web_device] Message sent to {sent_count} consoles for device {device_id}: type={msg_type}")
        return sent_count > 0

    async def broadcast_to_web_consoles(self, message: dict, subscribed_only: bool = False):
        """
        Broadcast a message to all connected Web Consoles.

        Args:
            message: The message to broadcast
            subscribed_only: If True, only send to consoles with subscriptions
        """
        msg_type = message.get("type", "unknown")
        sent_count = 0
        for console_id, websocket in list(self._web_consoles.items()):
            # Skip if subscribed_only and console has no subscriptions
            if subscribed_only:
                state = self._console_states.get(console_id)
                if not state or not state.subscribed_devices:
                    continue

            try:
                await websocket.send_json(message)
                sent_count += 1
            except Exception as e:
                logger.error(f"[ws_broadcast] Failed to send to console {console_id}: {e}")
                await self.unregister_web_console(console_id)

        logger.debug(f"[ws_broadcast] Message broadcast to {sent_count} consoles: type={msg_type}")

    async def broadcast_device_sync(self, devices: list) -> bool:
        """
        Broadcast device sync result to all Web Consoles.

        Args:
            devices: List of device info dicts with device_id and status

        Returns:
            True if sent successfully
        """
        message = {
            "type": "device_sync",
            "devices": devices,
        }
        await self.broadcast_to_web_consoles(message)
        logger.info(f"[broadcast_device_sync] Broadcast to all consoles, device_count={len(devices)}")
        return True

    def get_all_connected_device_ids(self) -> list:
        """
        Get list of all device IDs currently connected via WebSocket.

        Returns:
            List of device IDs
        """
        return list(self._device_connections.keys())

    @property
    def web_console_count(self) -> int:
        """Get total Web Console count"""
        return len(self._web_consoles)

    @property
    def connection_count(self) -> int:
        """Get total connection count"""
        return len(self.connections)

    @property
    def registered_device_count(self) -> int:
        """Get total registered device count"""
        return len(self._device_connections)


# Global WebSocket hub instance
ws_hub = WebSocketHub()
