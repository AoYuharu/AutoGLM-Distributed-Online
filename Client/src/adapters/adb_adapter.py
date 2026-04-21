"""
ADB Adapter - Android 设备适配器

参照 phone_agent/adb/connection.py 和 phone_agent/adb/device.py 实现
"""
import logging
import subprocess
import time
from typing import Optional
from dataclasses import dataclass

from src.adapters.android_app_index import AndroidAppIndex
from src.adapters.base import (
    DeviceAdapterBase,
    DeviceCapabilities,
    Platform,
    ActionResult,
)
from src.config.apps import get_app_aliases, get_package_name

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
        self._adb_keyboard_available: Optional[bool] = None
        self._android_app_index = AndroidAppIndex(self)
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

    def _run_adb_checked(self, args: list, action_name: str, **kwargs) -> subprocess.CompletedProcess:
        """运行关键 ADB 命令，并在失败时抛出异常。"""
        result = self._run_adb(args, capture_output=True, text=True, **kwargs)
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        failure_text = stderr or stdout

        failed = result.returncode != 0
        lower_text = failure_text.lower()
        if not failed and any(token in lower_text for token in ("error", "failed", "exception", "no devices")):
            failed = True

        if failed:
            detail = failure_text or f"adb exit code {result.returncode}"
            raise RuntimeError(f"{action_name}: {detail}")

        return result

    def _sleep_after_action(self, delay: float) -> None:
        """Sleep after a successful action execution."""
        time.sleep(delay)

    # === 文本输入辅助方法 ===

    @staticmethod
    def _shell_escape(text: str) -> str:
        """POSIX single-quote escaping for the Android device shell."""
        return "'" + text.replace("'", "'\\''") + "'"

    @staticmethod
    def _is_ascii(text: str) -> bool:
        """Check if text contains only ASCII characters."""
        try:
            text.encode('ascii')
            return True
        except UnicodeEncodeError:
            return False

    def _detect_adb_keyboard(self) -> bool:
        """Check if ADB Keyboard IME is installed on the device (cached)."""
        if self._adb_keyboard_available is not None:
            return self._adb_keyboard_available
        try:
            result = self._run_adb(
                ["shell", "ime", "list", "-a"],
                capture_output=True, text=True, timeout=5,
            )
            self._adb_keyboard_available = (
                result.returncode == 0
                and "com.android.adbkeyboard" in (result.stdout or "")
            )
        except Exception:
            self._adb_keyboard_available = False
        self._log("debug", f"[type_text] ADB Keyboard available: {self._adb_keyboard_available}")
        return self._adb_keyboard_available

    def _type_via_adb_keyboard(self, text: str) -> bool:
        """Tier 1: Input text via ADB Keyboard broadcast (supports all Unicode)."""
        try:
            self._run_adb_checked(
                ["shell", "ime set com.android.adbkeyboard/.AdbIME"],
                action_name="type_text(set_ime)",
            )
            escaped = self._shell_escape(text)
            self._run_adb_checked(
                ["shell", f"am broadcast -a ADB_INPUT_TEXT --es msg {escaped}"],
                action_name="type_text(broadcast)",
            )
            self._sleep_after_action(DEFAULT_KEYBOARD_SWITCH_DELAY)
            self._log("debug", f"[type_text] Tier 1 (ADB Keyboard) success")
            return True
        except Exception as e:
            self._log("warning", f"[type_text] ADB Keyboard broadcast failed: {e}")
            return False

    def _type_via_clipboard(self, text: str) -> bool:
        """Tier 2: Input text via clipboard set + paste (Android 12+ / API 31+)."""
        try:
            escaped = self._shell_escape(text)
            self._run_adb_checked(
                ["shell", f"cmd clipboard set_text {escaped}"],
                action_name="type_text(clipboard_set)",
            )
            self._run_adb_checked(
                ["shell", "input keyevent 279"],
                action_name="type_text(paste)",
            )
            self._sleep_after_action(DEFAULT_KEYBOARD_SWITCH_DELAY)
            self._log("debug", f"[type_text] Tier 2 (clipboard paste) success")
            return True
        except Exception as e:
            self._log("warning", f"[type_text] Clipboard paste failed: {e}")
            return False

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
        if not text:
            return ActionResult(False, False, "No text provided for type action")
        self.type_text(text)
        return ActionResult(True, False)

    def _handle_launch(self, action: dict) -> ActionResult:
        """处理启动应用"""
        app_name = action.get("app")
        resolved_dynamically = False
        package = None
        if app_name:
            package = get_package_name(app_name)
            if not package:
                package = self._resolve_dynamic_app_package(app_name)
                resolved_dynamically = package is not None
            if not package:
                return ActionResult(False, False, self._build_launch_failure_message(app_name=app_name))
        else:
            package = action.get("package")
            if not package:
                return ActionResult(False, False, "No app/package specified")

        try:
            success = self.launch_app(package)
        except Exception:
            if app_name and resolved_dynamically and self._retry_dynamic_launch(app_name, package):
                return ActionResult(True, False)
            raise

        if success:
            return ActionResult(True, False)

        if app_name and resolved_dynamically:
            success = self._retry_dynamic_launch(app_name, package)
            if success:
                return ActionResult(True, False)

        return ActionResult(
            False,
            False,
            self._build_launch_failure_message(app_name=app_name, package=package),
        )

    def _format_launch_suggestions(self, suggestions: list[tuple[str, list[str]]]) -> str:
        formatted: list[str] = []
        for package, labels in suggestions:
            display_labels = ", ".join(dict.fromkeys(label for label in labels if label))
            if display_labels:
                formatted.append(f"{display_labels} -> {package}")
            else:
                formatted.append(package)
        return "; ".join(formatted)

    def _build_launch_failure_message(self, app_name: str | None = None, package: str | None = None) -> str:
        target = app_name or package or "unknown"
        parts = [f"App not found: {target}"]

        alias_hints: list[str] = []
        if package:
            alias_hints = get_app_aliases(package)
        if alias_hints:
            alias_hint_text = ", ".join(dict.fromkeys(alias_hints))
            parts.append(f"Known aliases for {package}: {alias_hint_text}")

        suggestions = self._android_app_index.get_package_suggestions()
        if suggestions:
            parts.append(
                "Available app suggestions: "
                f"{self._format_launch_suggestions(suggestions)}"
            )

        if app_name and not suggestions and not alias_hints:
            parts.append(
                "No dynamic Android app-name mapping matched this request. "
                "Try a more exact installed app label."
            )

        return " | ".join(parts)

    def _resolve_dynamic_app_package(self, app_name: str) -> str | None:
        """Resolve Android app names using cached and refreshed dynamic indexes."""
        package = self._android_app_index.resolve(app_name)
        if package:
            return package

        self._android_app_index.load_cached()
        package = self._android_app_index.resolve(app_name)
        if package:
            return package

        self._android_app_index.refresh()
        return self._android_app_index.resolve(app_name)

    def _retry_dynamic_launch(self, app_name: str, package: str) -> bool:
        """Invalidate stale dynamic cache once and retry a resolved launch."""
        self._log(
            "warning",
            f"[launch] Dynamic package launch failed for device {self.device_id}: {app_name} -> {package}; refreshing index",
        )
        self._android_app_index.invalidate(package)
        self._android_app_index.refresh()
        refreshed_package = self._android_app_index.resolve(app_name)
        if not refreshed_package:
            return False
        return self.launch_app(refreshed_package)

    def _send_action_command(self, args: list, action_name: str, delay: float) -> None:
        """Execute a checked ADB action command and sleep on success."""
        self._run_adb_checked(args, action_name)
        self._sleep_after_action(delay)

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
        """点击"""
        self._send_action_command(
            ["shell", "input", "tap", str(x), str(y)],
            action_name="tap",
            delay=DEFAULT_TAP_DELAY,
        )

    def double_tap(self, x: int, y: int) -> None:
        """双击"""
        self._run_adb_checked(
            ["shell", "input", "tap", str(x), str(y)],
            action_name="double_tap",
        )
        time.sleep(0.05)  # 50ms 间隔
        self._run_adb_checked(
            ["shell", "input", "tap", str(x), str(y)],
            action_name="double_tap",
        )
        self._sleep_after_action(DEFAULT_TAP_DELAY)

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> None:
        """长按"""
        self._send_action_command(
            ["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)],
            action_name="long_press",
            delay=DEFAULT_TAP_DELAY,
        )

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 300
    ) -> None:
        """滑动"""
        self._send_action_command(
            [
                "shell", "input", "swipe",
                str(start_x), str(start_y),
                str(end_x), str(end_y),
                str(duration_ms)
            ],
            action_name="swipe",
            delay=DEFAULT_SWIPE_DELAY,
        )

    def back(self) -> None:
        """返回键"""
        self._send_action_command(
            ["shell", "input", "keyevent", "4"],
            action_name="back",
            delay=DEFAULT_BACK_DELAY,
        )

    def home(self) -> None:
        """Home 键"""
        self._send_action_command(
            ["shell", "input", "keyevent", "3"],
            action_name="home",
            delay=DEFAULT_HOME_DELAY,
        )

    def launch_app(self, package: str) -> bool:
        """启动应用"""
        if not package:
            return False

        self._send_action_command(
            [
                "shell", "monkey",
                "-p", package,
                "-c", "android.intent.category.LAUNCHER",
                "1"
            ],
            action_name="launch_app",
            delay=DEFAULT_LAUNCH_DELAY,
        )
        return True

    def type_text(self, text: str) -> None:
        """输入文本 (three-tier: ADB Keyboard → clipboard paste → adb input text)"""
        if not text:
            return

        # Tier 1: ADB Keyboard (supports all Unicode)
        if self._detect_adb_keyboard():
            if self._type_via_adb_keyboard(text):
                return

        # Tier 2: Clipboard paste (non-ASCII fallback)
        if not self._is_ascii(text):
            if self._type_via_clipboard(text):
                return
            # No method available for non-ASCII
            self._log("error", f"[type_text] Cannot input non-ASCII text without ADB Keyboard. "
                       "Install: https://github.com/nicholasgasior/android-keyboard")
            raise RuntimeError(
                "Non-ASCII text input requires ADB Keyboard (com.android.adbkeyboard). "
                "Install it on the device to enable Chinese text input."
            )

        # Tier 3: Standard adb input text (ASCII only, with proper escaping)
        escaped = text.replace(" ", "%s")
        self._send_action_command(
            ["shell", f"input text {self._shell_escape(escaped)}"],
            action_name="type_text",
            delay=DEFAULT_KEYBOARD_SWITCH_DELAY,
        )
