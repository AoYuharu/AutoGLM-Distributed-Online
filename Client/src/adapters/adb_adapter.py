"""
ADB Adapter - Android 设备适配器

参照 phone_agent/adb/connection.py 和 phone_agent/adb/device.py 实现
"""
import logging
import subprocess
import time
from typing import Optional
from dataclasses import dataclass

from src.adapters.base import (
    DeviceAdapterBase,
    DeviceCapabilities,
    Platform,
    ActionResult,
)

# 模块日志器
_logger = logging.getLogger(__name__)


# 默认延迟配置（秒）
DEFAULT_TAP_DELAY = 0.1
DEFAULT_SWIPE_DELAY = 0.1
DEFAULT_BACK_DELAY = 0.2
DEFAULT_HOME_DELAY = 0.2
DEFAULT_LAUNCH_DELAY = 2.0
DEFAULT_KEYBOARD_SWITCH_DELAY = 0.3


@dataclass
class ADBDeviceInfo:
    """ADB 设备信息"""
    device_id: str
    status: str
    model: Optional[str] = None


class ADBAdapter(DeviceAdapterBase):
    """
    Android 设备适配器

    通过 ADB 命令与 Android 设备通信，执行截图、输入、控制等操作。
    """

    def __init__(self, device_id: str, adb_path: str = "adb", logger: Optional[logging.Logger] = None):
        """
        初始化 ADB 适配器

        Args:
            device_id: ADB 设备序列号
            adb_path: adb 可执行文件路径
            logger: 可选的日志记录器
        """
        super().__init__(device_id, logger)
        self.adb_path = adb_path
        self._platform = Platform.ANDROID
        self._log("info", f"[ADBAdapter] Initialized for device {device_id}", extra={"device_id": device_id})

    @property
    def _adb_prefix(self) -> list:
        """ADB 命令前缀"""
        prefix = [self.adb_path]
        if self.device_id:
            prefix.extend(["-s", self.device_id])
        return prefix

    def _run_adb(self, args: list, **kwargs) -> subprocess.CompletedProcess:
        """运行 ADB 命令"""
        cmd = self._adb_prefix + args
        return subprocess.run(cmd, **kwargs)

    def _check_output(self, args: list, **kwargs) -> bytes:
        """运行 ADB 命令并返回输出"""
        cmd = self._adb_prefix + args
        return subprocess.check_output(cmd, **kwargs)

    # === 设备连接管理 ===

    @staticmethod
    def list_devices(adb_path: str = "adb") -> list[ADBDeviceInfo]:
        """
        列出所有连接的 ADB 设备

        Args:
            adb_path: adb 路径

        Returns:
            设备信息列表
        """
        try:
            result = subprocess.run(
                [adb_path, "devices", "-l"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            devices = []
            for line in result.stdout.strip().split("\n")[1:]:
                if not line.strip():
                    continue

                parts = line.split()
                if len(parts) >= 2:
                    device_id = parts[0]
                    status = parts[1]

                    model = None
                    for part in parts[2:]:
                        if part.startswith("model:"):
                            model = part.split(":", 1)[1]
                            break

                    devices.append(ADBDeviceInfo(
                        device_id=device_id,
                        status=status,
                        model=model,
                    ))

            return devices

        except Exception as e:
            print(f"Error listing devices: {e}")
            return []

    async def health_check(self) -> bool:
        """心跳检测"""
        self._log("debug", f"[health_check] Checking device {self.device_id}")
        try:
            result = self._run_adb(["shell", "echo", "ok"], timeout=5)
            success = result.returncode == 0
            self._log("debug", f"[health_check] Device {self.device_id} health check: {success}")
            return success
        except Exception as e:
            self._log("warning", f"[health_check] Device {self.device_id} health check failed: {e}")
            return False

    # === 能力检查 ===

    async def check_capabilities(self) -> DeviceCapabilities:
        """检查设备能力"""
        self._log("info", f"[check_capabilities] Checking capabilities for device {self.device_id}")
        if self._capabilities is not None:
            self._log("debug", f"[check_capabilities] Using cached capabilities for device {self.device_id}")
            return self._capabilities

        try:
            # 获取设备型号
            model = self._get_device_model()

            # 获取屏幕分辨率
            screen_size = self._get_screen_size()

            # 获取系统版本
            os_version = self._get_os_version()

            # 获取 API 级别
            api_level = self._get_api_level()

            # 获取已安装应用
            supported_apps = self._get_installed_apps()

            self._capabilities = DeviceCapabilities(
                platform=Platform.ANDROID,
                screenshot=True,
                input_text=True,
                system_buttons=["back", "home", "power"],
                battery=True,
                screen_size=screen_size,
                os_version=os_version,
                supported_apps=supported_apps,
                api_level=api_level,
                device_name=model,
            )

            self._log("info", f"[check_capabilities] Device {self.device_id} capabilities: model={model}, screen={screen_size}, os={os_version}",
                      extra={"device_id": self.device_id, "model": model, "screen_size": list(screen_size), "os_version": os_version})
            return self._capabilities

        except Exception as e:
            self._log("error", f"[check_capabilities] Device {self.device_id} failed: {e}")
            raise RuntimeError(f"Failed to check capabilities: {e}")

    def _get_device_model(self) -> str:
        """获取设备型号"""
        try:
            output = self._check_output(
                ["shell", "getprop", "ro.product.model"],
                encoding="utf-8"
            ).strip()
            # 处理 bytes 输出
            if isinstance(output, bytes):
                output = output.decode("utf-8").strip()
            return output or "Unknown"
        except Exception:
            return "Unknown"

    def _get_screen_size(self) -> tuple[int, int]:
        """获取屏幕分辨率"""
        try:
            width = self._check_output(
                ["shell", "wm", "size", "width"],
                encoding="utf-8"
            ).strip()
            height = self._check_output(
                ["shell", "wm", "size", "height"],
                encoding="utf-8"
            ).strip()

            return int(width), int(height)
        except Exception:
            return (1080, 2400)  # 默认值

    def _get_os_version(self) -> str:
        """获取系统版本"""
        try:
            version = self._check_output(
                ["shell", "getprop", "ro.build.version.release"],
                encoding="utf-8"
            )
            # 处理 bytes 输出
            if isinstance(version, bytes):
                version = version.decode("utf-8").strip()
            elif isinstance(version, str):
                version = version.strip()
            return f"Android {version}"
        except Exception:
            return "Android Unknown"

    def _get_api_level(self) -> Optional[int]:
        """获取 API 级别"""
        try:
            level = self._check_output(
                ["shell", "getprop", "ro.build.version.sdk"],
                encoding="utf-8"
            )
            # 处理 bytes 输出
            if isinstance(level, bytes):
                level = level.decode("utf-8").strip()
            elif isinstance(level, str):
                level = level.strip()
            return int(level)
        except Exception:
            return None

    def _get_installed_apps(self) -> list[str]:
        """获取已安装应用包名"""
        try:
            output = self._check_output(
                ["shell", "pm", "list", "packages", "-3"],
                encoding="utf-8"
            )
            # 处理 bytes 输出
            if isinstance(output, bytes):
                output = output.decode("utf-8")
            packages = []
            for line in output.strip().split("\n"):
                if line.startswith("package:"):
                    packages.append(line.replace("package:", "").strip())
            return packages[:100]  # 限制数量
        except Exception:
            return []

    # === 截图 ===

    def get_screenshot(self) -> bytes:
        """获取截图

        使用两种方式：
        1. 优先使用 screencap + adb pull
        2. 回退使用 adb exec-out (不需要临时文件)
        """
        import tempfile
        import os

        self._log("debug", f"[get_screenshot] Capturing screenshot for device {self.device_id}")
        start_time = time.time()

        # 方式1: 使用临时文件
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                local_path = f.name

            # 设备上的临时文件路径
            device_path = f"/sdcard/screenshot_{os.getpid()}.png"

            # 截图到设备临时目录
            result = self._run_adb(
                ["shell", "screencap", "-p", device_path],
                capture_output=True,
                timeout=10
            )

            if result.returncode == 0:
                # 拉取到本地
                pull_result = self._run_adb(
                    ["pull", device_path, local_path],
                    capture_output=True,
                    timeout=10
                )

                if pull_result.returncode == 0 and os.path.exists(local_path):
                    with open(local_path, "rb") as f:
                        data = f.read()

                    # 清理设备文件
                    try:
                        self._run_adb(["shell", "rm", device_path], capture_output=True)
                    except Exception:
                        pass

                    if len(data) > 0:
                        elapsed = (time.time() - start_time) * 1000
                        self._log("debug", f"[get_screenshot] Device {self.device_id} screenshot captured: {len(data)} bytes in {elapsed:.1f}ms")
                        return data

        except Exception as e:
            self._log("warning", f"[get_screenshot] Device {self.device_id} method 1 failed: {e}")

        # 方式2: 使用 exec-out (推荐，更快)
        try:
            result = self._run_adb(
                ["exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=10
            )

            if result.returncode == 0 and len(result.stdout) > 0:
                elapsed = (time.time() - start_time) * 1000
                self._log("debug", f"[get_screenshot] Device {self.device_id} screenshot captured via exec-out: {len(result.stdout)} bytes in {elapsed:.1f}ms")
                return result.stdout

        except Exception as e:
            self._log("warning", f"[get_screenshot] Device {self.device_id} method 2 failed: {e}")

        # 方式3: 使用现代 ADB 的 screenshot 命令
        try:
            result = self._run_adb(
                ["shell", "screenshot", "-", local_path if 'local_path' in dir() else "/tmp/screen.png"],
                capture_output=True,
                timeout=10
            )

            # 如果上面定义了 local_path，尝试读取
            if 'local_path' in dir() and os.path.exists(local_path):
                with open(local_path, "rb") as f:
                    data = f.read()
                if len(data) > 0:
                    elapsed = (time.time() - start_time) * 1000
                    self._log("debug", f"[get_screenshot] Device {self.device_id} screenshot captured via method 3: {len(data)} bytes in {elapsed:.1f}ms")
                    return data

        except Exception as e:
            self._log("warning", f"[get_screenshot] Device {self.device_id} method 3 failed: {e}")

        self._log("error", f"[get_screenshot] Device {self.device_id} all methods failed")
        raise RuntimeError("Failed to capture screenshot")

    # === 动作执行 ===

    def execute_action(self, action: dict) -> ActionResult:
        """执行动作"""
        start_time = time.time()
        action_type = self._resolve_action_type(action)
        action_name = self._normalize_action_name(action)

        self._log("debug", f"[execute_action] Device {self.device_id} executing: {action_name}",
                  extra={"device_id": self.device_id, "action": action_name, "action_full": action})

        # 处理 finish
        if action_type == "finish":
            self._log("info", f"[execute_action] Device {self.device_id} finish action: {action.get('message')}")
            return ActionResult(
                success=True,
                should_finish=True,
                message=action.get("message")
            )

        if action_type != "do":
            self._log("warning", f"[execute_action] Device {self.device_id} unknown action type: {action_type}")
            return ActionResult(
                success=False,
                should_finish=False,
                message=f"Unknown action type: {action_type}"
            )

        try:
            result: ActionResult
            if action_name == "tap":
                result = self._handle_tap(action)
            elif action_name == "double_tap":
                result = self._handle_double_tap(action)
            elif action_name == "long_press":
                result = self._handle_long_press(action)
            elif action_name == "swipe":
                result = self._handle_swipe(action)
            elif action_name == "back":
                result = self._handle_back(action)
            elif action_name == "home":
                result = self._handle_home(action)
            elif action_name in ("type", "type_name"):
                result = self._handle_type(action)
            elif action_name == "launch":
                result = self._handle_launch(action)
            elif action_name == "wait":
                result = self._handle_wait(action)
            else:
                self._log("warning", f"[execute_action] Device {self.device_id} unknown action: {action_name}")
                return ActionResult(
                    success=False,
                    should_finish=False,
                    message=f"Unknown action: {action_name}"
                )

            elapsed = (time.time() - start_time) * 1000
            self._log("info", f"[execute_action] Device {self.device_id} action completed: {action_name} ({elapsed:.1f}ms) success={result.success}",
                      extra={"device_id": self.device_id, "action": action_name, "success": result.success, "duration_ms": elapsed})
            return result

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            self._log("error", f"[execute_action] Device {self.device_id} action failed: {action_name} - {e} ({elapsed:.1f}ms)")
            return ActionResult(
                success=False,
                should_finish=False,
                message=f"Action failed: {e}"
            )

    def _handle_tap(self, action: dict) -> ActionResult:
        """处理点击"""
        element = action.get("element")
        if not element:
            return ActionResult(False, False, "No element coordinates")

        x, y = self._convert_coords(element)
        self.tap(x, y)
        return ActionResult(True, False)

    def _handle_double_tap(self, action: dict) -> ActionResult:
        """处理双击"""
        element = action.get("element")
        if not element:
            return ActionResult(False, False, "No element coordinates")

        x, y = self._convert_coords(element)
        self.double_tap(x, y)
        return ActionResult(True, False)

    def _handle_long_press(self, action: dict) -> ActionResult:
        """处理长按"""
        element = action.get("element")
        if not element:
            return ActionResult(False, False, "No element coordinates")

        x, y = self._convert_coords(element)
        duration = action.get("duration", 3000)
        self.long_press(x, y, duration)
        return ActionResult(True, False)

    def _handle_swipe(self, action: dict) -> ActionResult:
        """处理滑动"""
        start = action.get("start")
        end = action.get("end")
        if not start or not end:
            return ActionResult(False, False, "Missing swipe coordinates")

        start_x, start_y = self._convert_coords(start)
        end_x, end_y = self._convert_coords(end)
        duration = action.get("duration", 300)

        self.swipe(start_x, start_y, end_x, end_y, duration)
        return ActionResult(True, False)

    def _handle_back(self, action: dict) -> ActionResult:
        """处理返回"""
        self.back()
        return ActionResult(True, False)

    def _handle_home(self, action: dict) -> ActionResult:
        """处理 Home"""
        self.home()
        return ActionResult(True, False)

    def _handle_type(self, action: dict) -> ActionResult:
        """处理文本输入"""
        text = action.get("text", "")
        self.type_text(text)
        return ActionResult(True, False)

    def _handle_launch(self, action: dict) -> ActionResult:
        """处理启动应用"""
        package = action.get("app") or action.get("package")
        if not package:
            return ActionResult(False, False, "No app/package specified")

        success = self.launch_app(package)
        if success:
            return ActionResult(True, False)
        return ActionResult(False, False, f"App not found: {package}")

    def _handle_wait(self, action: dict) -> ActionResult:
        """处理等待"""
        duration = float(action.get("duration", "1").replace("seconds", "").strip())
        time.sleep(duration)
        return ActionResult(True, False)

    def _convert_coords(self, element: dict) -> tuple[int, int]:
        """转换坐标"""
        if not self._capabilities:
            return element.get("x", 500), element.get("y", 500)

        screen_width, screen_height = self._capabilities.screen_size
        return self._convert_relative_to_absolute(element, screen_width, screen_height)

    # === 基础操作方法 ===

    def tap(self, x: int, y: int) -> None:
        """点击"""
        self._run_adb(
            ["shell", "input", "tap", str(x), str(y)],
            capture_output=True
        )
        time.sleep(DEFAULT_TAP_DELAY)

    def double_tap(self, x: int, y: int) -> None:
        """双击"""
        self._run_adb(
            ["shell", "input", "tap", str(x), str(y)],
            capture_output=True
        )
        time.sleep(0.05)  # 50ms 间隔
        self._run_adb(
            ["shell", "input", "tap", str(x), str(y)],
            capture_output=True
        )
        time.sleep(DEFAULT_TAP_DELAY)

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> None:
        """长按"""
        self._run_adb(
            ["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)],
            capture_output=True
        )
        time.sleep(DEFAULT_TAP_DELAY)

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 300
    ) -> None:
        """滑动"""
        self._run_adb(
            [
                "shell", "input", "swipe",
                str(start_x), str(start_y),
                str(end_x), str(end_y),
                str(duration_ms)
            ],
            capture_output=True
        )
        time.sleep(DEFAULT_SWIPE_DELAY)

    def back(self) -> None:
        """返回键"""
        self._run_adb(
            ["shell", "input", "keyevent", "4"],
            capture_output=True
        )
        time.sleep(DEFAULT_BACK_DELAY)

    def home(self) -> None:
        """Home 键"""
        self._run_adb(
            ["shell", "input", "keyevent", "3"],
            capture_output=True
        )
        time.sleep(DEFAULT_HOME_DELAY)

    def launch_app(self, package: str) -> bool:
        """启动应用"""
        if not package:
            return False

        self._run_adb(
            [
                "shell", "monkey",
                "-p", package,
                "-c", "android.intent.category.LAUNCHER",
                "1"
            ],
            capture_output=True
        )
        time.sleep(DEFAULT_LAUNCH_DELAY)
        return True

    def type_text(self, text: str) -> None:
        """输入文本"""
        # 需要 ADB Keyboard 或其他输入法
        self._run_adb(
            ["shell", "input", "text", text.replace(" ", "%s")],
            capture_output=True
        )
