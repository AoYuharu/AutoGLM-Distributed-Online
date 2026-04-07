"""
Minimal Device Client - Acts as an executor for the server-side Agent.

This client:
1. Connects to the server
2. Sends heartbeats and screenshots
3. Receives action commands
4. Executes actions via ADB/HDC/WDA
5. Reports execution results

ALL INTELLIGENCE IS ON THE SERVER SIDE.
"""
import asyncio
import json
import time
import base64
import subprocess
from datetime import datetime
from typing import Optional
import httpx
import websockets
from dataclasses import dataclass


# Configuration
SERVER_URL = "http://localhost:8000"
DEVICE_ID = "10AE551838000D7"
WS_URL = "ws://localhost:8000/ws"


@dataclass
class DeviceInfo:
    """Device information."""
    device_id: str
    platform: str = "android"
    model: str = ""
    os_version: str = ""
    screen_width: int = 1080
    screen_height: int = 2400


class ADBDevice:
    """ADB device controller."""

    def __init__(self, device_id: str):
        self.device_id = device_id

    def _run(self, command: str) -> str:
        """Run ADB command."""
        result = subprocess.run(
            f"adb -s {self.device_id} {command}",
            shell=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip() or result.stderr.strip()

    def get_screenshot(self) -> bytes:
        """Capture screenshot."""
        result = subprocess.run(
            f"adb -s {self.device_id} exec-out screencap -p",
            shell=True,
            capture_output=True
        )
        return result.stdout

    def get_current_app(self) -> str:
        """Get current foreground app."""
        return self._run("shell dumpsys window | grep mCurrentFocus")

    def tap(self, x: int, y: int):
        """Tap at coordinates."""
        # Convert relative (0-999) to actual coordinates
        actual_x = int(x / 999 * self._get_width())
        actual_y = int(y / 999 * self._get_height())
        self._run(f"shell input tap {actual_x} {actual_y}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300):
        """Swipe from (x1,y1) to (x2,y2)."""
        w, h = self._get_width(), self._get_height()
        ax1, ay1 = int(x1 / 999 * w), int(y1 / 999 * h)
        ax2, ay2 = int(x2 / 999 * w), int(y2 / 999 * h)
        self._run(f"shell input swipe {ax1} {ay1} {ax2} {ay2} {duration}")

    def input_text(self, text: str):
        """Input text."""
        # Escape special characters
        text = text.replace(" ", "%s")
        self._run(f'shell input text "{text}"')

    def press_back(self):
        """Press back button."""
        self._run("shell input keyevent KEYCODE_BACK")

    def press_home(self):
        """Press home button."""
        self._run("shell input keyevent KEYCODE_HOME")

    def launch_app(self, app_name: str):
        """Launch app by name."""
        # Map app names to package names
        app_map = {
            "微信": "com.tencent.mm",
            "wechat": "com.tencent.mm",
            "设置": "com.android.settings",
            "settings": "com.android.settings",
            "淘宝": "com.taobao.taobao",
            "taobao": "com.taobao.taobao",
            "抖音": "com.ss.android.ugc.aweme",
            "douyin": "com.ss.android.ugc.aweme",
        }

        package = app_map.get(app_name, app_name)
        # Try launching by package name
        self._run(f"shell am start -n {package}/.ui.MainActivity")
        # If that doesn't work, try with package name directly
        if "error" in self._run("").lower():
            self._run(f"shell monkey -p {package} -c android.intent.category.LAUNCHER 1")

    def wait(self, duration_ms: int):
        """Wait for specified milliseconds."""
        time.sleep(duration_ms / 1000)

    def _get_width(self) -> int:
        """Get screen width."""
        size = self._run("shell wm size").split(":")[-1].strip()
        if "x" in size:
            return int(size.split("x")[0])
        return 1080

    def _get_height(self) -> int:
        """Get screen height."""
        size = self._run("shell wm size").split(":")[-1].strip()
        if "x" in size:
            return int(size.split("x")[-1])
        return 2400

    def get_device_info(self) -> dict:
        """Get device info."""
        model = self._run("shell getprop ro.product.model")
        version = self._run("shell getprop ro.build.version.release")
        return {
            "model": model,
            "os_version": f"Android {version}",
            "screen_width": self._get_width(),
            "screen_height": self._get_height(),
        }


class MinimalDeviceClient:
    """
    Minimal client that acts as an executor.
    - Registers with server
    - Sends heartbeats and screenshots
    - Receives and executes action commands
    - Reports results back
    """

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.device = ADBDevice(device_id)
        self.server_url = SERVER_URL
        self.ws_url = WS_URL
        self.running = False
        self.session_id = None
        self.client = httpx.Client(timeout=30.0)
        # 防抖机制 (文档1.4)
        self._last_offline_report = 0
        self._offline_cooldown = 10  # 离线上报冷却时间（秒）

    def register(self) -> dict:
        """Register device with server (auto-creates or updates)."""
        # First check if device is actually connected
        if not self.is_device_connected():
            print(f"[注册] 设备未连接或 ADB 不可用")
            self.update_device_status("offline")
            return {}

        info = self.device.get_device_info()
        data = {
            "device_id": self.device_id,
            "platform": "android",
            **info,
            "capabilities": {
                "touch": True,
                "screenshot": True,
                "input": True,
            }
        }

        print(f"[注册] 设备信息: {info}")

        response = self.client.post(
            f"{self.server_url}/api/v1/devices/{self.device_id}/register",
            json=data
        )

        if response.status_code == 200:
            result = response.json()
            print(f"[注册成功] Device ID: {result.get('device_id')}, Status: {result.get('status')}")
            return result
        else:
            print(f"[注册失败] {response.status_code}: {response.text}")
            return {}

    def is_device_connected(self) -> bool:
        """Check if device is actually connected via ADB."""
        try:
            result = subprocess.run(
                f"adb -s {self.device_id} get-state",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            output = (result.stdout + result.stderr).lower()
            # Must contain "device" but NOT "not found"
            return "device" in output and "not found" not in output
        except Exception:
            return False

    def heartbeat(self):
        """Send heartbeat to server only if device is connected."""
        try:
            if not self.is_device_connected():
                now = time.time()
                # 防抖：只有在超过冷却时间后才上报离线
                if now - self._last_offline_report > self._offline_cooldown:
                    print(f"[设备断开] 手机未连接或 ADB 未就绪")
                    self.update_device_status("offline")
                    self._last_offline_report = now
                return False
            self.client.post(f"{self.server_url}/api/v1/devices/{self.device_id}/heartbeat")
            return True
        except Exception as e:
            print(f"[心跳失败] {e}")
            return False

    def get_pending_tasks(self) -> list:
        """Get pending tasks for this device."""
        try:
            response = self.client.get(
                f"{self.server_url}/api/v1/tasks",
                params={"status": "pending", "device_id": self.device_id}
            )
            if response.status_code == 200:
                return response.json().get("tasks", [])
        except Exception as e:
            print(f"[获取任务失败] {e}")
        return []

    def report_step(
        self,
        task_id: str,
        step_number: int,
        action_type: str,
        action_params: dict,
        thinking: str = "",
        success: bool = True,
        error: str = "",
    ):
        """Report a step to server."""
        try:
            self.client.post(
                f"{self.server_url}/api/v1/tasks/{task_id}/steps",
                json={
                    "step_number": step_number,
                    "action_type": action_type,
                    "action_params": action_params,
                    "thinking": thinking,
                    "success": success,
                    "error": error,
                }
            )
        except Exception as e:
            print(f"[上报步骤失败] {e}")

    def update_task_status(self, task_id: str, status: str, current_step: int = 0, result: dict = None):
        """Update task status."""
        try:
            self.client.post(
                f"{self.server_url}/api/v1/tasks/{task_id}/update",
                json={
                    "current_step": current_step,
                    "status": status,
                    **(result or {})
                }
            )
        except Exception as e:
            print(f"[更新状态失败] {e}")

    def update_device_status(self, status: str, current_task_id: str = None):
        """Update device status."""
        try:
            self.client.post(
                f"{self.server_url}/api/v1/devices/{self.device_id}/status",
                json={
                    "status": status,
                    "current_task_id": current_task_id,
                }
            )
        except Exception as e:
            print(f"[更新设备状态失败] {e}")

    def execute_action(self, action: dict) -> dict:
        """Execute an action on the device."""
        action_type = action.get("action", "").lower()
        params = {k: v for k, v in action.items() if k != "action"}

        print(f"[执行动作] {action_type} - {params}")

        try:
            if action_type == "tap":
                x, y = params.get("x", 500), params.get("y", 500)
                self.device.tap(x, y)
                return {"success": True, "message": f"Tapped at ({x}, {y})"}

            elif action_type == "double_tap":
                x, y = params.get("x", 500), params.get("y", 500)
                self.device.tap(x, y)
                time.sleep(0.1)
                self.device.tap(x, y)
                return {"success": True, "message": f"Double-tapped at ({x}, {y})"}

            elif action_type == "long_press":
                x, y = params.get("x", 500), params.get("y", 500)
                duration = params.get("duration", 500)
                self.device.swipe(x, y, x, y, duration)
                return {"success": True, "message": f"Long-pressed at ({x}, {y})"}

            elif action_type == "swipe":
                x1, y1 = params.get("x1", 500), params.get("y1", 500)
                x2, y2 = params.get("x2", 500), params.get("y2", 500)
                duration = params.get("duration", 300)
                self.device.swipe(x1, y1, x2, y2, duration)
                return {"success": True, "message": f"Swiped from ({x1},{y1}) to ({x2},{y2})"}

            elif action_type == "input" or action_type == "type":
                text = params.get("text", "")
                self.device.input_text(text)
                return {"success": True, "message": f"Input text: {text[:20]}..."}

            elif action_type == "launch":
                app = params.get("app", "")
                self.device.launch_app(app)
                return {"success": True, "message": f"Launched app: {app}"}

            elif action_type == "back":
                self.device.press_back()
                return {"success": True, "message": "Back button pressed"}

            elif action_type == "home":
                self.device.press_home()
                return {"success": True, "message": "Home button pressed"}

            elif action_type == "wait":
                duration = params.get("duration", 1000)
                self.device.wait(duration)
                return {"success": True, "message": f"Waited {duration}ms"}

            elif action_type == "finish":
                return {"success": True, "message": "Task finished", "_finish": True}

            else:
                return {"success": False, "message": f"Unknown action: {action_type}"}

        except Exception as e:
            return {"success": False, "message": str(e), "error": str(e)}

    async def run_executor_loop(self):
        """
        Main executor loop - simple registration and heartbeat.
        1. Register device (auto-creates or updates)
        2. Send heartbeats
        3. Poll for tasks and execute
        """
        print(f"\n{'='*60}")
        print(f"[设备执行器启动] Device ID: {self.device_id}")
        print(f"[服务器] {self.server_url}")
        print(f"{'='*60}\n")

        # Initial registration
        self.register()
        device_was_connected = self.is_device_connected()

        self.running = True
        current_task = None

        while self.running:
            try:
                # Check device connection status
                device_connected = self.is_device_connected()

                if device_connected and not device_was_connected:
                    # Device reconnected - re-register
                    print("[检测到] 手机重新连接，重新注册...")
                    self.register()
                    device_was_connected = True

                elif not device_connected and device_was_connected:
                    # Device disconnected - report offline
                    print("[检测到] 手机已断开")
                    self.update_device_status("offline")
                    device_was_connected = False

                # Only send heartbeat and check tasks if device is connected
                if device_connected:
                    # Send heartbeat
                    self.heartbeat()

                    # Check for pending tasks
                    tasks = self.get_pending_tasks()

                    if tasks and not current_task:
                        # Start new task
                        task = tasks[0]
                        current_task = task
                        print(f"\n[新任务] {task['task_id']}: {task['instruction']}")

                        # Update statuses
                        self.update_device_status("busy", task['task_id'])
                        self.update_task_status(task['task_id'], "running", 0)

                        # Report initial state
                        screenshot = self.device.get_screenshot()
                        screenshot_b64 = base64.b64encode(screenshot).decode()

                        print(f"[任务开始] 等待服务器 Agent 推理...")
                        await asyncio.sleep(1)

                    elif current_task:
                        # Execute task steps
                        screenshot = self.device.get_screenshot()
                        current_app = self.device.get_current_app()

                        await asyncio.sleep(2)

                    else:
                        # No task, just heartbeat
                        await asyncio.sleep(3)
                else:
                    # Device not connected, wait and check again
                    await asyncio.sleep(2)

            except KeyboardInterrupt:
                print("\n[停止] 用户中断")
                self.running = False
            except Exception as e:
                print(f"[错误] {e}")
                await asyncio.sleep(5)

        # Cleanup
        self.update_device_status("offline")
        print("[退出] 执行器已停止")


async def main():
    """Main entry point."""
    client = MinimalDeviceClient(DEVICE_ID)
    await client.run_executor_loop()


if __name__ == "__main__":
    asyncio.run(main())
