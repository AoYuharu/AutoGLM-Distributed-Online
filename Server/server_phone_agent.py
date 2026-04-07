"""
Server-connected PhoneAgent client for full integration testing.
This client connects to the server, polls for tasks, and executes them.
"""
import asyncio
import httpx
import json
import time
import base64
from datetime import datetime
from pathlib import Path

# Server configuration
SERVER_URL = "http://localhost:8000"
DEVICE_ID = "10AE551838000D7"

# Agent configuration - Zhipu AI
MODEL_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
MODEL_NAME = "autoglm-phone"
MODEL_API_KEY = "50a4f78ab3da43bb86a87f7119eb9f5b.wVYBHi0inGFHGMjE"

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from phone_agent import PhoneAgent
from phone_agent.model import ModelConfig
from phone_agent.agent import AgentConfig


class ServerPhoneAgent:
    """PhoneAgent that connects to the distributed server."""

    def __init__(self, device_id: str, server_url: str = SERVER_URL):
        self.device_id = device_id
        self.server_url = server_url
        self.current_task_id = None
        self.current_task = None
        self.client = httpx.Client(timeout=30.0)

    def register_device(self):
        """Register device to server."""
        # Get device info via ADB
        import subprocess

        result = subprocess.run(
            ['adb', '-s', self.device_id, 'shell', 'getprop', 'ro.product.model'],
            capture_output=True, text=True
        )
        model = result.stdout.strip()

        result = subprocess.run(
            ['adb', '-s', self.device_id, 'shell', 'getprop', 'ro.build.version.release'],
            capture_output=True, text=True
        )
        version = f"Android {result.stdout.strip()}"

        result = subprocess.run(
            ['adb', '-s', self.device_id, 'shell', 'wm', 'size'],
            capture_output=True, text=True
        )
        size_output = result.stdout.strip()
        if 'Physical size:' in size_output:
            size = size_output.split('Physical size:')[1].strip().split('x')
            width, height = int(size[0]), int(size[1])
        else:
            width, height = 1080, 2400

        data = {
            "device_id": self.device_id,
            "platform": "android",
            "model": model,
            "os_version": version,
            "screen_width": width,
            "screen_height": height,
            "capabilities": {
                "touch": True,
                "screenshot": True,
                "input": True,
            }
        }

        print(f"[注册设备] 型号: {model}, 系统: {version}")
        response = self.client.post(
            f"{self.server_url}/api/v1/devices/{self.device_id}/register",
            json=data
        )
        return response.json()

    def heartbeat(self):
        """Send heartbeat to server."""
        response = self.client.post(
            f"{self.server_url}/api/v1/devices/{self.device_id}/heartbeat"
        )
        return response.json()

    def get_pending_tasks(self):
        """Get pending tasks for this device."""
        response = self.client.get(
            f"{self.server_url}/api/v1/tasks",
            params={"status": "pending", "device_id": self.device_id}
        )
        return response.json().get("tasks", [])

    def get_task(self, task_id: str):
        """Get task details."""
        response = self.client.get(f"{self.server_url}/api/v1/tasks/{task_id}")
        return response.json()

    def update_task_progress(self, task_id: str, current_step: int, status: str, result: dict = None):
        """Update task progress on server."""
        data = {
            "current_step": current_step,
            "status": status,
        }
        if result:
            data["result"] = result

        response = self.client.post(
            f"{self.server_url}/api/v1/tasks/{task_id}/update",
            json=data
        )
        return response.json()

    def add_task_step(self, task_id: str, step_number: int, action_type: str,
                     action_params: dict, thinking: str = None,
                     success: bool = True, error: str = None, screenshot_url: str = None):
        """Add a step to task on server."""
        data = {
            "step_number": step_number,
            "action_type": action_type,
            "action_params": action_params,
            "thinking": thinking,
            "success": success,
        }
        if error:
            data["error"] = error
        if screenshot_url:
            data["screenshot_url"] = screenshot_url

        response = self.client.post(
            f"{self.server_url}/api/v1/tasks/{task_id}/steps",
            json=data
        )
        return response.json()

    def upload_logs(self, logs: list):
        """Upload logs to server."""
        response = self.client.post(
            f"{self.server_url}/api/v1/logs/{self.device_id}/upload",
            json={"logs": logs}
        )
        return response.json()

    def update_device_status(self, status: str, current_task_id: str = None):
        """Update device status."""
        data = {
            "status": status,
            "current_task_id": current_task_id,
        }
        response = self.client.post(
            f"{self.server_url}/api/v1/devices/{self.device_id}/status",
            json=data
        )
        return response.json()

    def run_agent_task(self, task_id: str, instruction: str, mode: str = "normal"):
        """Run a task using PhoneAgent."""
        print(f"\n{'='*60}")
        print(f"[Agent 开始执行] 任务ID: {task_id}")
        print(f"[指令] {instruction}")
        print(f"[模式] {'谨慎模式' if mode == 'cautious' else '普通模式'}")
        print(f"{'='*60}\n")

        self.current_task_id = task_id
        self.current_task = self.get_task(task_id)

        # Update task status to running
        self.update_task_progress(task_id, 0, "running")
        self.update_device_status("busy", task_id)

        # Upload log
        self.upload_logs([{
            "timestamp": datetime.now().isoformat(),
            "log_type": "task_start",
            "level": "info",
            "message": f"任务开始: {instruction}",
            "details": {"task_id": task_id, "mode": mode}
        }])

        # Initialize PhoneAgent
        model_config = ModelConfig(
            base_url=MODEL_BASE_URL,
            model_name=MODEL_NAME,
            api_key=MODEL_API_KEY,
        )

        # Cautious mode callback - poll server for decision
        def cautious_callback(action_desc: str) -> bool:
            """In cautious mode, wait for user decision from server."""
            print(f"\n[谨慎模式] 等待用户确认...")
            print(f"[动作] {action_desc}")

            # In real implementation, this would poll for user decision
            # For now, auto-confirm for testing
            return True

        agent_config = AgentConfig(
            max_steps=50,
            device_id=self.device_id,
            verbose=True,
        )

        agent = PhoneAgent(
            model_config=model_config,
            agent_config=agent_config,
            confirmation_callback=cautious_callback if mode == "cautious" else None,
        )

        # Execute task
        step_count = 0
        try:
            result = agent.run(instruction)
            step_count = agent._step_count

            # Task completed
            self.update_task_progress(task_id, step_count, "completed", {
                "result": result,
                "total_steps": step_count
            })

            self.upload_logs([{
                "timestamp": datetime.now().isoformat(),
                "log_type": "task_complete",
                "level": "success",
                "message": f"任务完成，共执行 {step_count} 步",
                "details": {"task_id": task_id, "total_steps": step_count, "result": result}
            }])

            print(f"\n✅ 任务完成! 结果: {result}")

        except Exception as e:
            error_msg = str(e)
            print(f"\n❌ 任务失败: {error_msg}")

            self.update_task_progress(task_id, step_count, "failed", {
                "error": error_msg
            })

            self.upload_logs([{
                "timestamp": datetime.now().isoformat(),
                "log_type": "task_failed",
                "level": "error",
                "message": f"任务失败: {error_msg}",
                "details": {"task_id": task_id, "error": error_msg}
            }])

        finally:
            self.update_device_status("idle")
            self.current_task_id = None
            self.current_task = None

        return step_count

    def poll_and_execute(self, poll_interval: int = 2):
        """Poll for tasks and execute them."""
        print(f"\n[Agent 客户端] 开始轮询任务 (间隔 {poll_interval}秒)")
        print(f"[设备] {self.device_id}")
        print(f"[服务器] {self.server_url}")
        print("-" * 50)

        while True:
            try:
                # Send heartbeat
                self.heartbeat()

                # Check for pending tasks
                tasks = self.get_pending_tasks()

                if tasks:
                    task = tasks[0]  # Take the first pending task
                    self.run_agent_task(
                        task_id=task["task_id"],
                        instruction=task["instruction"],
                        mode=task.get("mode", "normal")
                    )
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 无待执行任务，继续等待...")

                time.sleep(poll_interval)

            except KeyboardInterrupt:
                print("\n\n[Agent 客户端] 已停止")
                break
            except Exception as e:
                print(f"[错误] {e}")
                time.sleep(poll_interval)


def main():
    """Main entry point."""
    agent = ServerPhoneAgent(DEVICE_ID)

    # Register device
    result = agent.register_device()
    print(f"[注册结果] {result.get('status', 'unknown')}")

    # Start polling and executing
    agent.poll_and_execute(poll_interval=3)


if __name__ == "__main__":
    main()
