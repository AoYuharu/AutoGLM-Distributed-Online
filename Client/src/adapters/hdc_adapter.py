"""
HDC Adapter - HarmonyOS 设备适配器

参照 phone_agent/hdc/connection.py 和 phone_agent/hdc/device.py 实现
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


@dataclass
class HDCDeviceInfo:
    """HDC 设备信息"""
    device_id: str
    status: str
    model: Optional[str] = None


def _run_hdc_command(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """运行 HDC 命令"""
    return subprocess.run(cmd, **kwargs)


class HDCAdapter(DeviceAdapterBase):
    """
    HarmonyOS 设备适配器

    通过 HDC 命令与 HarmonyOS 设备通信。
    """

    def __init__(self, device_id: str, hdc_path: str = "hdc", logger: Optional[logging.Logger] = None):
        super().__init__(device_id, logger)
        self.hdc_path = hdc_path
        self._platform = Platform.HARMONYOS
        self._log("info", f"[HDCAdapter] Initialized for device {device_id}", extra={"device_id": device_id})

    @property
    def _hdc_prefix(self) -> list:
        """HDC 命令前缀"""
        prefix = [self.hdc_path]
        if self.device_id:
            prefix.extend(["-t", self.device_id])
        return prefix

    def _run_hdc(self, args: list, **kwargs) -> subprocess.CompletedProcess:
        """运行 HDC 命令"""
        cmd = self._hdc_prefix + args
        return _run_hdc_command(cmd, **kwargs)

    def _check_output(self, args: list, **kwargs) -> str:
        """运行 HDC 命令并返回输出"""
        cmd = self._hdc_prefix + args
        result = subprocess.run(cmd, capture_output=True, **kwargs)
        return result.stdout.decode("utf-8") if isinstance(result.stdout, bytes) else result.stdout

    # === 设备列表 ===

    @staticmethod
    def list_devices(hdc_path: str = "hdc") -> list[HDCDeviceInfo]:
        """
        列出所有连接的 HDC 设备

        Args:
            hdc_path: hdc 路径

        Returns:
            设备信息列表
        """
        try:
            result = subprocess.run(
                [hdc_path, "list", "targets"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            devices = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue

                device_id = line.strip()
                devices.append(HDCDeviceInfo(
                    device_id=device_id,
                    status="device",
                ))

            return devices

        except Exception as e:
            print(f"Error listing devices: {e}")
            return []

    async def health_check(self) -> bool:
        """心跳检测"""
        self._log("debug", f"[health_check] Checking device {self.device_id}")
        try:
            result = self._run_hdc(["shell", "echo", "ok"], timeout=5)
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

            # 获取已安装应用
            supported_apps = self._get_installed_apps()

            self._capabilities = DeviceCapabilities(
                platform=Platform.HARMONYOS,
                screenshot=True,
                input_text=True,
                system_buttons=["back", "home", "power"],
                battery=True,
                screen_size=screen_size,
                os_version=os_version,
                supported_apps=supported_apps,
                device_name=model,
            )

            self._log("info", f"[check_capabilities] Device {self.device_id} capabilities: model={model}, screen={screen_size}, os={os_version}")
            return self._capabilities

        except Exception as e:
            self._log("error", f"[check_capabilities] Device {self.device_id} failed: {e}")
            raise RuntimeError(f"Failed to check capabilities: {e}")

    def _get_device_model(self) -> str:
        """获取设备型号"""
        try:
            output = self._check_output(
                ["shell", "getprop", "ro.product.model"],
            )
            return output.strip() or "Unknown"
        except Exception:
            return "Unknown"

    def _get_screen_size(self) -> tuple[int, int]:
        """获取屏幕分辨率"""
        try:
            output = self._check_output(["shell", "wm", "size"])
            # 格式可能是 "Physical size: 1080x2400" 或 "1080x2400"
            size_str = output.strip().split(":")[-1].strip()
            if "x" in size_str:
                width, height = size_str.split("x")
                return int(width), int(height)
            return (1080, 2400)
        except Exception:
            return (1080, 2400)

    def _get_os_version(self) -> str:
        """获取系统版本"""
        try:
            output = self._check_output(
                ["shell", "getprop", "ro.build.version.release"],
            )
            return f"HarmonyOS {output.strip()}"
        except Exception:
            return "HarmonyOS Unknown"

    def _get_installed_apps(self) -> list[str]:
        """获取已安装应用包名"""
        try:
            output = self._check_output(
                ["shell", "bm", "dump", "-a"],
            )
            packages = []
            for line in output.strip().split("\n"):
                if line.startswith("package:"):
                    packages.append(line.replace("package:", "").strip())
            return packages[:100]
        except Exception:
            return []

    # === 截图 ===

    def get_screenshot(self) -> bytes:
        """获取截图"""
        import tempfile
        import os

        self._log("debug", f"[get_screenshot] Capturing screenshot for device {self.device_id}")
        start_time = time.time()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            temp_path = f.name

        try:
            # 使用 screencap 命令截图
            self._run_hdc(
                ["shell", "screencap", "-p", temp_path],
                capture_output=True
            )

            # 拉取到本地
            self._run_hdc(
                ["file", "recv", temp_path, temp_path],
                capture_output=True
            )

            with open(temp_path, "rb") as f:
                data = f.read()

            elapsed = (time.time() - start_time) * 1000
            self._log("debug", f"[get_screenshot] Device {self.device_id} screenshot captured: {len(data)} bytes in {elapsed:.1f}ms")
            return data

        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            try:
                self._run_hdc(["shell", "rm", temp_path], capture_output=True)
            except Exception:
                pass

    # === 动作执行 ===

    def execute_action(self, action: dict) -> ActionResult:
        """执行动作"""
        start_time = time.time()
        action_type = self._resolve_action_type(action)
        action_name = self._normalize_action_name(action)

        self._log("debug", f"[execute_action] Device {self.device_id} executing: {action_name}",
                   extra={"device_id": self.device_id, "action": action_name, "action_full": action})

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

    def _resolve_point(self, action: dict) -> Optional[dict]:
        """解析点击类动作坐标，兼容 element 和顶层 x/y。"""
        element = action.get("element")
        if element:
            return element

        if "x" in action and "y" in action:
            return {"x": action.get("x"), "y": action.get("y")}

        return None

    def _resolve_swipe_points(self, action: dict) -> tuple[Optional[dict], Optional[dict]]:
        """解析滑动坐标，兼容 start/end 和顶层 x1/y1/x2/y2。"""
        start = action.get("start")
        end = action.get("end")
        if start and end:
            return start, end

        if all(key in action for key in ("x1", "y1", "x2", "y2")):
            return (
                {"x": action.get("x1"), "y": action.get("y1")},
                {"x": action.get("x2"), "y": action.get("y2")},
            )

        return None, None

    def _handle_tap(self, action: dict) -> ActionResult:
        """处理点击"""
        point = self._resolve_point(action)
        if not point:
            return ActionResult(False, False, "No element coordinates")

        x, y = self._convert_coords(point)
        self.tap(x, y)
        return ActionResult(True, False)

    def _handle_double_tap(self, action: dict) -> ActionResult:
        """处理双击"""
        point = self._resolve_point(action)
        if not point:
            return ActionResult(False, False, "No element coordinates")

        x, y = self._convert_coords(point)
        self.double_tap(x, y)
        return ActionResult(True, False)

    def _handle_long_press(self, action: dict) -> ActionResult:
        """处理长按"""
        point = self._resolve_point(action)
        if not point:
            return ActionResult(False, False, "No element coordinates")

        x, y = self._convert_coords(point)
        duration = action.get("duration", 3000)
        self.long_press(x, y, duration)
        return ActionResult(True, False)

    def _handle_swipe(self, action: dict) -> ActionResult:
        """处理滑动"""
        start, end = self._resolve_swipe_points(action)
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
        raw_duration = action.get("duration", 1)
        if isinstance(raw_duration, str):
            duration = float(raw_duration.replace("seconds", "").strip())
        else:
            duration = float(raw_duration)
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
        """点击 - 使用 uitest uiInput click"""
        self._run_hdc(
            ["shell", "uitest", "uiInput", "click", str(x), str(y)],
            capture_output=True
        )
        time.sleep(DEFAULT_TAP_DELAY)

    def double_tap(self, x: int, y: int) -> None:
        """双击 - 使用 uitest uiInput doubleClick"""
        self._run_hdc(
            ["shell", "uitest", "uiInput", "doubleClick", str(x), str(y)],
            capture_output=True
        )
        time.sleep(DEFAULT_TAP_DELAY)

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> None:
        """长按 - 使用 uitest uiInput longClick"""
        self._run_hdc(
            ["shell", "uitest", "uiInput", "longClick", str(x), str(y)],
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
        """滑动 - 使用 uitest uiInput swipe"""
        self._run_hdc(
            [
                "shell", "uitest", "uiInput", "swipe",
                str(start_x), str(start_y),
                str(end_x), str(end_y),
                str(duration_ms)
            ],
            capture_output=True
        )
        time.sleep(DEFAULT_SWIPE_DELAY)

    def back(self) -> None:
        """返回键 - 使用 uitest uiInput keyEvent Back"""
        self._run_hdc(
            ["shell", "uitest", "uiInput", "keyEvent", "Back"],
            capture_output=True
        )
        time.sleep(DEFAULT_BACK_DELAY)

    def home(self) -> None:
        """Home 键 - 使用 uitest uiInput keyEvent Home"""
        self._run_hdc(
            ["shell", "uitest", "uiInput", "keyEvent", "Home"],
            capture_output=True
        )
        time.sleep(DEFAULT_HOME_DELAY)

    def launch_app(self, package: str) -> bool:
        """启动应用 - 使用 aa start"""
        if not package:
            return False

        # HarmonyOS 使用 aa start -b {bundle} -a {ability}
        self._run_hdc(
            ["shell", "aa", "start", "-b", package, "-a", "EntryAbility"],
            capture_output=True
        )
        time.sleep(DEFAULT_LAUNCH_DELAY)
        return True

    def type_text(self, text: str) -> None:
        """输入文本"""
        self._run_hdc(
            ["shell", "uitest", "uiInput", "inputText", text],
            capture_output=True
        )
