"""
Simplified WebSocket API routes for Server <-> Client communication

Simplified protocol (no device_register):
- Client connects with device_id as query parameter
- Server auto-registers device on connection
- Only handles: ack, action_cmd

Web Console (Server -> Web):
- Web Console connects with console_id as query parameter
- Used for broadcasting agent_step, agent_status, session_locked, etc.
"""

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import structlog

from src.services.websocket import ws_hub
from src.services.device_status_manager import device_status_manager, DeviceStatus
from src.logging_config import get_ws_console_logger

logger = structlog.get_logger()
ws_console_logger = get_ws_console_logger()
router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    device_id: str = Query(None, description="Device ID"),
    client_id: str = Query(None, description="Client ID (optional, if not passed device_id is treated as client_id)"),
):
    if not device_id and not client_id:
        await websocket.close(code=4000, reason="device_id or client_id is required as query parameter")
        return

    connection_id = await ws_hub.connect(websocket)

    if client_id:
        actual_device_id = device_id if device_id and device_id != client_id else None
        actual_client_id = client_id
    else:
        actual_device_id = None
        actual_client_id = device_id

    ws_hub.register_device(connection_id, actual_device_id, actual_client_id, capabilities={})
    ws_hub.connection_states[connection_id].is_authenticated = True

    if actual_device_id:
        from src.services.react_scheduler import scheduler

        active_task = scheduler.get_task(actual_device_id)
        if active_task and active_task.is_active:
            await device_status_manager.update_status(actual_device_id, DeviceStatus.BUSY, active_task.task_id)
        else:
            await device_status_manager.set_idle(actual_device_id)

    logger.info(
        "Device connected via WebSocket",
        connection_id=connection_id,
        device_id=device_id,
        actual_device_id=actual_device_id,
    )

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "accepted": True,
                "device_id": device_id,
            }
        )

        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                await handle_ws_message(connection_id, message)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error("WebSocket error", error=str(e))
                await websocket.send_json({"type": "error", "message": str(e)})
    except WebSocketDisconnect:
        pass
    finally:
        state = ws_hub.connection_states.get(connection_id)
        bound_device_id = state.device_id if state else actual_device_id
        if bound_device_id:
            from src.services.react_scheduler import scheduler

            await scheduler.cleanup_disconnected_device(bound_device_id)
        await ws_hub.disconnect(connection_id, reason="disconnected")
        logger.info(
            "Device disconnected",
            connection_id=connection_id,
            device_id=device_id,
            bound_device_id=bound_device_id,
        )


async def handle_ws_message(connection_id: str, message: dict):
    msg_type = message.get("type")
    state = ws_hub.connection_states.get(connection_id)
    payload = message.get("payload") or {}
    device_id = payload.get("device_id") or message.get("device_id") or (state.device_id if state else None)

    if msg_type == "ack":
        from src.services.action_router import action_router
        from src.services.react_scheduler import scheduler

        payload = message.get("payload") or {}
        accepted = payload.get("accepted", True)
        error = payload.get("error")

        if action_router:
            await action_router.handle_ack(message)
        ref_msg_id = message.get("ref_msg_id") or payload.get("ref_msg_id")
        if device_id and ref_msg_id and scheduler:
            await scheduler.handle_bootstrap_ack(
                device_id,
                ref_msg_id,
                accepted=accepted,
                error=error,
            )
        if device_id:
            await device_status_manager.touch(device_id)
        logger.debug(
            "ACK received from client",
            device_id=device_id,
            msg_id=message.get("msg_id"),
            accepted=accepted,
        )
    else:
        logger.debug(
            "Unknown message type received",
            device_id=device_id,
            msg_type=msg_type,
        )


@router.get("/ws/status")
async def ws_status():
    return {
        "connections": ws_hub.connection_count,
        "registered_devices": ws_hub.registered_device_count,
        "web_consoles": ws_hub.web_console_count,
    }


@router.websocket("/ws/console")
async def websocket_console_endpoint(
    websocket: WebSocket,
    console_id: str = Query(None, description="Console ID for Web interface"),
):
    if not console_id:
        await websocket.close(code=4000, reason="console_id is required as query parameter")
        return

    await ws_hub.register_web_console(websocket, console_id)
    ws_console_logger.info(f"[ws_console_connect] Web Console connected: {console_id}")

    await websocket.send_json(
        {
            "type": "connected",
            "accepted": True,
            "console_id": console_id,
        }
    )

    try:
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                await handle_console_message(console_id, message)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error("[ws_console] WebSocket error", error=str(e))
                await websocket.send_json({"type": "error", "message": str(e)})
    except WebSocketDisconnect:
        pass
    finally:
        await ws_hub.unregister_web_console(console_id)
        ws_console_logger.info(f"[ws_console_disconnect] Web Console disconnected: {console_id}")


async def handle_console_message(console_id: str, message: dict):
    msg_type = message.get("type")

    if msg_type == "subscribe":
        device_id = message.get("device_id")
        if device_id:
            ws_hub.subscribe_console_to_device(console_id, device_id)
            ws_console_logger.debug(
                f"[ws_console_subscribe] Console {console_id} subscribed to device {device_id}"
            )
            if console_id in ws_hub._web_consoles:
                await ws_hub._web_consoles[console_id].send_json(
                    {"type": "subscribed", "device_id": device_id}
                )

    elif msg_type == "unsubscribe":
        device_id = message.get("device_id")
        if device_id:
            ws_hub.unsubscribe_console_from_device(console_id, device_id)
            ws_console_logger.debug(
                f"[ws_console_unsubscribe] Console {console_id} unsubscribed from device {device_id}"
            )

    elif msg_type == "sync":
        ws_console_logger.info(f"[ws_console_sync] Sync requested: {console_id}")
        connected_device_ids = ws_hub.get_all_connected_device_ids()
        sync_result = await device_status_manager.sync_all_devices(connected_device_ids)
        if sync_result["changed"] or True:
            all_entries = await device_status_manager.get_all_devices()
            sync_devices = [
                {
                    "device_id": device_id,
                    "status": entry.status.value,
                    "last_update": entry.last_update.isoformat(),
                }
                for device_id, entry in all_entries.items()
            ]
            await ws_hub.broadcast_device_sync(sync_devices)
            ws_console_logger.info(
                f"[ws_console_sync] Sync broadcast completed: console={console_id}, "
                f"total={len(sync_devices)}, changed={len(sync_result['changed'])}"
            )

    elif msg_type == "create_task":
        device_id = message.get("device_id")
        instruction = message.get("instruction")
        mode = message.get("mode", "normal")
        max_steps = message.get("max_steps", 100)
        max_observe_error_retries = message.get("max_observe_error_retries", 2)

        ws_console_logger.info(
            f"[ws_console_create_task] Creating task: console={console_id}, device={device_id}, "
            f"instruction={instruction[:50] if instruction else 'None'}..., max_steps={max_steps}, mode={mode}"
        )

        logger.info(
            "Console create_task received",
            console_id=console_id,
            device_id=device_id,
            instruction=instruction,
            max_steps=max_steps,
            mode=mode,
            max_observe_error_retries=max_observe_error_retries,
        )

        if not device_id or not instruction:
            if console_id in ws_hub._web_consoles:
                await ws_hub._web_consoles[console_id].send_json(
                    {"type": "error", "message": "device_id and instruction are required"}
                )
            return

        try:
            import uuid
            from src.services.react_scheduler import scheduler

            task_id = f"task_{uuid.uuid4().hex[:12]}"
            acquired = await device_status_manager.try_acquire_task(device_id, task_id)
            if not acquired:
                entry = await device_status_manager.get_entry(device_id)
                current_status = entry.status.value if entry else "unknown"
                if console_id in ws_hub._web_consoles:
                    await ws_hub._web_consoles[console_id].send_json(
                        {
                            "type": "error",
                            "message": f"Device {device_id} is not available (status: {current_status})",
                        }
                    )
                return

            scheduler.submit_task(
                device_id=device_id,
                task_id=task_id,
                instruction=instruction,
                mode=mode,
                max_steps=max_steps,
                max_observe_error_retries=max_observe_error_retries,
            )

            ws_console_logger.info(f"[ws_console_create_task] Task created: {task_id}")
            logger.info(
                "Console task created",
                console_id=console_id,
                task_id=task_id,
                device_id=device_id,
                instruction=instruction,
                max_steps=max_steps,
                max_observe_error_retries=max_observe_error_retries,
            )
            if console_id in ws_hub._web_consoles:
                await ws_hub._web_consoles[console_id].send_json(
                    {
                        "type": "task_created",
                        "task_id": task_id,
                        "device_id": device_id,
                        "status": "pending",
                    }
                )
        except Exception as e:
            ws_console_logger.error(f"[ws_console_create_task] Error: {e}")
            if console_id in ws_hub._web_consoles:
                await ws_hub._web_consoles[console_id].send_json(
                    {"type": "error", "message": f"Failed to create task: {str(e)}"}
                )

    elif msg_type == "interrupt_task":
        device_id = message.get("device_id")
        task_id = message.get("task_id")

        ws_console_logger.info(
            f"[ws_console_interrupt_task] Interrupting task: device={device_id}, task_id={task_id}"
        )

        try:
            from src.services.react_scheduler import scheduler

            await scheduler.interrupt_task(device_id)

            if console_id in ws_hub._web_consoles:
                await ws_hub._web_consoles[console_id].send_json(
                    {
                        "type": "task_interrupted",
                        "task_id": task_id,
                        "device_id": device_id,
                    }
                )
        except Exception as e:
            ws_console_logger.error(f"[ws_console_interrupt_task] Error: {e}")
            if console_id in ws_hub._web_consoles:
                await ws_hub._web_consoles[console_id].send_json(
                    {"type": "error", "message": f"Failed to interrupt task: {str(e)}"}
                )

    elif msg_type == "confirm_phase":
        device_id = message.get("device_id")
        approved = message.get("approved", False)

        ws_console_logger.info(f"[ws_console_confirm_phase] device={device_id}, approved={approved}")

        from src.services.react_scheduler import scheduler

        scheduler.confirm_phase(device_id, approved)

        if console_id in ws_hub._web_consoles:
            await ws_hub._web_consoles[console_id].send_json(
                {"type": "phase_confirmed", "device_id": device_id, "approved": approved}
            )

    elif msg_type == "observe_error_decision":
        device_id = message.get("device_id")
        decision = message.get("decision")
        advice = message.get("advice", "")

        ws_console_logger.info(
            f"[ws_console_observe_error_decision] console={console_id}, device={device_id}, decision={decision}"
        )

        if not device_id or decision not in {"continue", "interrupt"}:
            if console_id in ws_hub._web_consoles:
                await ws_hub._web_consoles[console_id].send_json(
                    {"type": "error", "message": "device_id and decision(continue|interrupt) are required"}
                )
            return

        from src.services.react_scheduler import scheduler

        handled = await scheduler.resolve_observe_error_decision(device_id, decision, advice)
        if console_id in ws_hub._web_consoles:
            await ws_hub._web_consoles[console_id].send_json(
                {
                    "type": "observe_error_decision_applied",
                    "device_id": device_id,
                    "decision": decision,
                    "advice": advice,
                    "success": handled,
                }
            )

    else:
        ws_console_logger.debug(
            f"[ws_console_message] Unknown message type: console={console_id}, msg_type={msg_type}"
        )
