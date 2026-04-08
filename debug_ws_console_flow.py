import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import httpx
import websockets


async def fetch_json(client: httpx.AsyncClient, path: str) -> dict:
    response = await client.get(path)
    response.raise_for_status()
    return response.json()


async def download_file(client: httpx.AsyncClient, path: str, target_dir: Path) -> Path | None:
    response = await client.get(path)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    disposition_name = path.rstrip("/").split("/")[-1] or "artifact.bin"
    filename = response.headers.get("content-disposition", "")
    if "filename=" in filename:
        disposition_name = filename.split("filename=")[-1].strip('"')
    target_path = target_dir / disposition_name
    target_path.write_bytes(response.content)
    return target_path


async def main() -> int:
    parser = argparse.ArgumentParser(description="Headless /ws/console debug flow")
    parser.add_argument("--server", default="http://localhost:8000", help="Server base URL")
    parser.add_argument("--device-id", required=True, help="Real device ID")
    parser.add_argument("--instruction", required=True, help="Task instruction")
    parser.add_argument("--mode", default="normal", help="Task mode")
    parser.add_argument("--max-steps", type=int, default=20, help="Task max steps")
    parser.add_argument("--timeout", type=int, default=300, help="Overall wait timeout in seconds")
    parser.add_argument("--download-dir", default="debug_artifacts", help="Artifact download directory")
    args = parser.parse_args()

    server = args.server.rstrip("/")
    ws_url = server.replace("http://", "ws://").replace("https://", "wss://") + "/ws/console"
    console_id = f"debug_console_{uuid.uuid4().hex[:8]}"
    download_dir = Path(args.download_dir) / args.device_id / time.strftime("%Y%m%d_%H%M%S")
    download_dir.mkdir(parents=True, exist_ok=True)

    print(f"[debug] server={server}")
    print(f"[debug] device_id={args.device_id}")
    print(f"[debug] instruction={args.instruction}")
    print(f"[debug] artifacts_dir={download_dir}")

    async with httpx.AsyncClient(base_url=server, timeout=30.0) as client:
        health = await fetch_json(client, "/health")
        ws_status = await fetch_json(client, "/ws/status")
        devices = await fetch_json(client, "/api/v1/devices")
        print("[health]", json.dumps(health, ensure_ascii=False))
        print("[ws_status]", json.dumps(ws_status, ensure_ascii=False))
        print("[devices]", json.dumps(devices, ensure_ascii=False))

        task_id = None
        final_status = None
        started = time.time()

        async with websockets.connect(
            f"{ws_url}?console_id={console_id}",
            max_size=None,
        ) as ws:
            print("[ws] connected", await ws.recv())
            await ws.send(json.dumps({"type": "subscribe", "device_id": args.device_id}, ensure_ascii=False))
            print("[ws] subscribe sent")

            await ws.send(
                json.dumps(
                    {
                        "type": "create_task",
                        "device_id": args.device_id,
                        "instruction": args.instruction,
                        "mode": args.mode,
                        "max_steps": args.max_steps,
                    },
                    ensure_ascii=False,
                )
            )
            print("[ws] create_task sent")

            while time.time() - started < args.timeout:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                except asyncio.TimeoutError:
                    await ws.send(json.dumps({"type": "sync"}))
                    print("[ws] sync sent")
                    continue

                message = json.loads(raw)
                msg_type = message.get("type")
                if msg_type == "agent_step":
                    summary = {
                        "type": msg_type,
                        "task_id": message.get("task_id"),
                        "device_id": message.get("device_id"),
                        "step_number": message.get("step_number"),
                        "success": message.get("success"),
                        "action": message.get("action"),
                        "reasoning_preview": str(message.get("reasoning", ""))[:200],
                        "result_preview": str(message.get("result", ""))[:200],
                        "has_screenshot": bool(message.get("screenshot")),
                        "screenshot_length": len(message.get("screenshot") or ""),
                    }
                    print("[event]", json.dumps(summary, ensure_ascii=False))
                else:
                    print("[event]", json.dumps(message, ensure_ascii=False))

                if msg_type == "task_created":
                    task_id = message.get("task_id")
                elif msg_type == "agent_status":
                    status = message.get("status")
                    if status in {"completed", "failed", "interrupted"}:
                        final_status = status
                        break

        if not task_id:
            print("[error] task was not created")
            return 1

        print(f"[result] task_id={task_id}, final_status={final_status}")

        session = await fetch_json(client, f"/api/v1/devices/{args.device_id}/session")
        chat = await fetch_json(client, f"/api/v1/devices/{args.device_id}/chat")
        history = await fetch_json(client, f"/api/v1/devices/{args.device_id}/history")
        artifacts = await fetch_json(client, f"/api/v1/devices/{args.device_id}/artifacts")

        (download_dir / "session.json").write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        (download_dir / "chat.json").write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")
        (download_dir / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        (download_dir / "artifacts.json").write_text(json.dumps(artifacts, ensure_ascii=False, indent=2), encoding="utf-8")

        latest_screenshot = await download_file(
            client,
            f"/api/v1/devices/{args.device_id}/artifacts/screenshot/latest",
            download_dir,
        )
        latest_log = await download_file(
            client,
            f"/api/v1/devices/{args.device_id}/artifacts/logs/latest",
            download_dir,
        )
        react_records = await download_file(
            client,
            f"/api/v1/devices/{args.device_id}/artifacts/react-records",
            download_dir,
        )
        chat_history = await download_file(
            client,
            f"/api/v1/devices/{args.device_id}/artifacts/chat-history",
            download_dir,
        )

        print(f"[saved] session={download_dir / 'session.json'}")
        print(f"[saved] chat={download_dir / 'chat.json'}")
        print(f"[saved] history={download_dir / 'history.json'}")
        print(f"[saved] artifacts={download_dir / 'artifacts.json'}")
        print(f"[saved] latest_screenshot={latest_screenshot}")
        print(f"[saved] latest_log={latest_log}")
        print(f"[saved] react_records={react_records}")
        print(f"[saved] chat_history={chat_history}")

        return 0 if final_status == "completed" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("[debug] interrupted")
        raise SystemExit(130)
