"""
Ultra-minimal Device Executor Client

This client ONLY executes actions and sends screenshots.
ALL intelligence (Agent reasoning) runs on the SERVER.

Flow:
1. Register with server
2. Enter command mode via terminal or Web
3. Server runs Agent reasoning
4. Server sends action commands
5. Client executes and reports back
"""
import asyncio
import json
import base64
import time
import subprocess
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
import httpx


# Configuration
SERVER_URL = "http://localhost:8000"
DEVICE_ID = "10AE551838000D7"


class ADBExecutor:
    """Minimal ADB executor for executing actions."""

    def __init__(self, device_id: str):
        self.device_id = device_id

    def _run(self, cmd: str) -> str:
        result = subprocess.run(
            f"adb -s {self.device_id} {cmd}",
            shell=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip()

    def screenshot(self) -> str:
        """Get screenshot as base64."""
        result = subprocess.run(
            f"adb -s {self.device_id} exec-out screencap -p",
            shell=True,
            capture_output=True
        )
        return base64.b64encode(result.stdout).decode()

    def current_app(self) -> str:
        """Get current app info."""
        output = self._run("shell dumpsys window | findstr mCurrentFocus")
        return output

    def tap(self, x: int, y: int):
        width, height = self._screen_size()
        ax, ay = int(x / 999 * width), int(y / 999 * height)
        self._run(f"shell input tap {ax} {ay}")

    def swipe(self, x1, y1, x2, y2, duration=300):
        width, height = self._screen_size()
        ax1, ay1 = int(x1 / 999 * width), int(y1 / 999 * height)
        ax2, ay2 = int(x2 / 999 * width), int(y2 / 999 * height)
        self._run(f"shell input swipe {ax1} {ay1} {ax2} {ay2} {duration}")

    def input_text(self, text: str):
        text = text.replace(" ", "%s").replace("&", "\\&").replace("<", "\\<")
        self._run(f'shell input text "{text}"')

    def back(self):
        self._run("shell input keyevent KEYCODE_BACK")

    def home(self):
        self._run("shell input keyevent KEYCODE_HOME")

    def launch(self, app_name: str):
        app_map = {
            "微信": "com.tencent.mm", "wechat": "com.tencent.mm",
            "设置": "com.android.settings", "settings": "com.android.settings",
            "淘宝": "com.taobao.taobao",
            "抖音": "com.ss.android.ugc.aweme",
            "美团": "com.sg.android.tourcard",
        }
        package = app_map.get(app_name, app_name)
        self._run(f"shell am start -n {package}/.ui.MainActivity")

    def _screen_size(self):
        output = self._run("shell wm size")
        if "Physical size:" in output:
            parts = output.split("Physical size:")[1].strip().split("x")
            return int(parts[0]), int(parts[1])
        return 1080, 2400


class ExecutorClient:
    """
    Minimal executor that:
    - Registers with server
    - Polls for action commands from server
    - Executes actions via ADB
    - Reports results
    """

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.executor = ADBExecutor(device_id)
        self.http = httpx.Client(timeout=30.0)
        self.running = False
        self.current_task_id = None

    def register(self):
        """Register device with server."""
        info = self._get_device_info()
        data = {
            "device_id": self.device_id,
            "platform": "android",
            **info,
            "capabilities": {"touch": True, "screenshot": True, "input": True}
        }

        print(f"[注册] {info}")
        r = self.http.post(f"{SERVER_URL}/api/v1/devices/{self.device_id}/register", json=data)
        print(f"[注册结果] {r.status_code}")
        return r.json()

    def _get_device_info(self):
        model = self.executor._run("shell getprop ro.product.model")
        version = self.executor._run("shell getprop ro.build.version.release")
        w, h = self.executor._screen_size()
        return {"model": model, "os_version": f"Android {version}", "screen_width": w, "screen_height": h}

    def heartbeat(self):
        try:
            self.http.post(f"{SERVER_URL}/api/v1/devices/{self.device_id}/heartbeat")
        except:
            pass

    def create_session(self):
        """Create Agent session on server."""
        r = self.http.post(f"{SERVER_URL}/api/v1/agent/{self.device_id}/sessions")
        if r.status_code == 200:
            return r.json()
        return None

    def get_session(self):
        """Get current session state."""
        r = self.http.get(f"{SERVER_URL}/api/v1/agent/{self.device_id}/sessions")
        if r.status_code == 200:
            return r.json()
        return None

    def start_task(self, task_id: str, instruction: str, mode: str = "normal"):
        """Start a task on server."""
        r = self.http.post(
            f"{SERVER_URL}/api/v1/agent/{self.device_id}/tasks",
            params={"task_id": task_id, "instruction": instruction, "mode": mode}
        )
        if r.status_code == 200:
            return r.json()
        return None

    def execute_step(self):
        """Execute one step - capture screenshot, send to server, get action."""
        r = self.http.post(f"{SERVER_URL}/api/v1/agent/{self.device_id}/step")
        if r.status_code == 200:
            return r.json()
        return None

    def confirm_action(self, step_number: int, decision: str):
        """Confirm/reject pending action."""
        r = self.http.post(
            f"{SERVER_URL}/api/v1/agent/{self.device_id}/confirm",
            params={"step_number": step_number, "decision": decision}
        )
        if r.status_code == 200:
            return r.json()
        return None

    def execute_action(self, action: dict) -> dict:
        """Execute action on device."""
        action_type = action.get("action", "").lower()
        params = {k: v for k, v in action.items() if k != "action"}

        print(f"  [执行] {action_type}: {params}")

        try:
            if action_type == "tap":
                self.executor.tap(params.get("x", 500), params.get("y", 500))
                return {"success": True}

            elif action_type == "swipe":
                self.executor.swipe(
                    params.get("x1", 500), params.get("y1", 500),
                    params.get("x2", 500), params.get("y2", 500),
                    params.get("duration", 300)
                )
                return {"success": True}

            elif action_type == "input":
                self.executor.input_text(params.get("text", ""))
                return {"success": True}

            elif action_type == "back":
                self.executor.back()
                return {"success": True}

            elif action_type == "home":
                self.executor.home()
                return {"success": True}

            elif action_type == "launch":
                self.executor.launch(params.get("app", ""))
                return {"success": True}

            elif action_type == "wait":
                time.sleep(params.get("duration", 1000) / 1000)
                return {"success": True}

            elif action_type == "finish":
                return {"success": True, "finished": True}

            else:
                return {"success": False, "error": f"Unknown: {action_type}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_device_status(self, status: str, task_id: str = None):
        try:
            self.http.post(
                f"{SERVER_URL}/api/v1/devices/{self.device_id}/status",
                json={"status": status, "current_task_id": task_id}
            )
        except:
            pass

    async def run_interactive(self):
        """Interactive mode - user types commands."""
        print(f"\n{'='*60}")
        print(f"[交互模式] 设备: {self.device_id}")
        print(f"[输入指令] 输入你想让AI在手机上执行的任务")
        print(f"[示例] 打开设置")
        print(f"{'='*60}\n")

        # Register
        self.register()

        # Create session
        session = self.create_session()
        print(f"[会话] Session ID: {session.get('session_id')}")

        while True:
            try:
                # Get command
                cmd = input("\n指令> ").strip()
                if not cmd:
                    continue

                if cmd.lower() in ["exit", "quit", "退出"]:
                    break

                # Send to server to start task
                task_id = f"task_{int(time.time())}"
                self.start_task(task_id, cmd, mode="normal")

                # Execute loop
                print(f"[开始执行任务] {task_id}")
                self.update_device_status("busy", task_id)

                step = 0
                while True:
                    step += 1
                    print(f"\n--- Step {step} ---")

                    # Get next action from server
                    result = self.execute_step()

                    if not result:
                        print("[完成] 无更多步骤")
                        break

                    action_type = result.get("type")
                    action = result.get("action", {})

                    if action_type == "finish":
                        print(f"[完成] {action.get('message', 'Task finished')}")
                        break

                    if action_type == "pending":
                        print(f"[待确认] {action}")
                        decision = input("确认? (y/n/s=跳过): ").strip().lower()
                        if decision == "y":
                            self.confirm_action(result.get("step_number"), "confirm")
                        elif decision == "s":
                            self.confirm_action(result.get("step_number"), "skip")
                        else:
                            self.confirm_action(result.get("step_number"), "reject")
                        continue

                    if action_type == "executed":
                        print(f"[执行] {action}")
                        exec_result = self.execute_action(action)
                        if exec_result.get("finished"):
                            print(f"[完成]")
                            break

                    # Check status
                    session = self.get_session()
                    if session and session.get("status") == "completed":
                        print(f"[任务完成]")
                        break

                    await asyncio.sleep(1)

                self.update_device_status("idle")

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[错误] {e}")

        self.update_device_status("offline")
        print("\n[退出]")


async def main():
    client = ExecutorClient(DEVICE_ID)
    await client.run_interactive()


if __name__ == "__main__":
    asyncio.run(main())
