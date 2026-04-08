"""
WDA Adapter - iOS 设备适配器 (WebDriverAgent)

参照 phone_agent/xctest/connection.py 和 phone_agent/xctest/device.py 实现

WDA 使用 HTTP REST API 与 iOS 设备通信，主要端点：
- POST /session/:sessionId/wda/tap/element/:elementId
- POST /session/:sessionId/wda/touch/multi/perform
- GET /session/:sessionId/screenshot
"""
import asyncio
import logging
import time
import base64
import requests
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

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


class WDAAdapter(DeviceAdapterBase):
    """
    iOS 设备适配器 (通过 WebDriverAgent)

    通过 WebDriverAgent 与 iOS 设备通信。
    """

    def __init__(
        self,
        device_id: str,
        wda_url: str = "http://localhost:8100",
        session_timeout: int = 300,
        logger: Optional[logging.Logger] = None,
    ):
        """
        初始化 WDA 适配器

        Args:
            device_id: 设备 UDID
            wda_url: WDA 服务地址
            session_timeout: 会话超时时间（秒）
            logger: 可选的日志记录器
        """
        super().__init__(device_id, logger)
        self.wda_url = wda_url.rstrip("/")
        self.session_timeout = session_timeout
        self._platform = Platform.IOS
        self._session_id: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._log("info", f"[WDAAdapter] Initialized for device {device_id} at {wda_url}", extra={"device_id": device_id})

    async def _ensure_session(self) -> str:
        """确保 WDA 会话存在"""
        import aiohttp

        if self._session_id:
            return self._session_id

        async with aiohttp.ClientSession() as session:
            # 创建设话
            payload = {
                "desiredCapabilities": {
                    "platformName": "iOS",
                    "deviceName": "iPhone",
                    "udid": self.device_id,
                    "automationName": "XCUITest",
                }
            }

            async with session.post(
                f"{self.wda_url}/session",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.session_timeout),
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise RuntimeError(f"WDA session creation failed: {text}")

                data = await response.json()
                self._session_id = data.get("sessionId")

        return self._session_id

    async def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
    ) -> dict:
        """
        发送 WDA 请求

        Args:
            method: HTTP 方法
            path: 请求路径
            data: 请求数据

        Returns:
            响应数据
        """
        import aiohttp

        await self._ensure_session()

        url = f"{self.wda_url}{path}"
        kwargs = {"timeout": aiohttp.ClientTimeout(total=30)}

        if data:
            kwargs["json"] = data

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, **kwargs) as response:
                text = await response.text()

                if response.status >= 400:
                    raise RuntimeError(f"WDA request failed: {response.status} - {text}")

                try:
                    return await response.json()
                except Exception:
                    return {"response": text}

    async def health_check(self) -> bool:
        """心跳检测"""
        self._log("debug", f"[health_check] Checking device {self.device_id}")
        try:
            await self._request("GET", "/status")
            self._log("debug", f"[health_check] Device {self.device_id} health check: True")
            return True
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
            # 获取设备信息
            device_info = await self._get_device_info()

            # 获取屏幕分辨率
            screen_size = await self._get_screen_size()

            # 获取已安装应用
            supported_apps = await self._get_installed_apps()

            self._capabilities = DeviceCapabilities(
                platform=Platform.IOS,
                screenshot=True,
                input_text=True,
                system_buttons=["back", "home"],  # iOS 没有返回键，但模拟器可能有
                battery=False,  # WDA 不直接支持
                screen_size=screen_size,
                os_version=device_info.get("os_version", ""),
                supported_apps=supported_apps,
                device_name=device_info.get("device_name"),
            )

            self._log("info", f"[check_capabilities] Device {self.device_id} capabilities: device={device_info.get('device_name')}, screen={screen_size}, os={device_info.get('os_version')}")
            return self._capabilities

        except Exception as e:
            self._log("error", f"[check_capabilities] Device {self.device_id} failed: {e}")
            raise RuntimeError(f"Failed to check capabilities: {e}")

    async def _get_device_info(self) -> dict:
        """获取设备信息"""
        try:
            result = await self._request("GET", "/wda/device/info")
            return {
                "device_name": result.get("value", {}).get("deviceName", "iPhone"),
                "os_version": result.get("value", {}).get("sdkVersion", ""),
            }
        except Exception:
            # 尝试通过 source 获取基本信息
            return {
                "device_name": "iPhone",
                "os_version": "",
            }

    async def _get_screen_size(self) -> tuple[int, int]:
        """获取屏幕分辨率"""
        try:
            result = await self._request("GET", "/window/size")
            width = result.get("value", {}).get("width", 390)
            height = result.get("value", {}).get("height", 844)
            return int(width), int(height)
        except Exception:
            return (390, 844)  # iPhone 14 默认尺寸

    async def _get_installed_apps(self) -> list[str]:
        """获取已安装应用（模拟实现，WDA 不直接支持）"""
        # WDA 的 App Management 功能需要额外支持
        return []

    # === 截图 ===

    def get_screenshot(self) -> bytes:
        """获取截图"""
        self._log("debug", f"[get_screenshot] Capturing screenshot for device {self.device_id}")
        start_time = time.time()

        try:
            url = f"{self.wda_url}/screenshot"
            response = requests.get(url, timeout=30)

            if response.status_code != 200:
                raise RuntimeError(f"Screenshot failed: {response.status_code}")

            data = response.json()
            # WDA 返回 base64 编码的 PNG
            screenshot_base64 = data.get("value", "")
            result = base64.b64decode(screenshot_base64)

            elapsed = (time.time() - start_time) * 1000
            self._log("debug", f"[get_screenshot] Device {self.device_id} screenshot captured: {len(result)} bytes in {elapsed:.1f}ms")
            return result

        except Exception as e:
            self._log("error", f"[get_screenshot] Device {self.device_id} failed: {e}")
            raise RuntimeError(f"Failed to capture screenshot: {e}")

    def get_screenshot_async(self) -> bytes:
        """异步获取截图（备选实现）"""
        url = f"{self.wda_url}/screenshot"
        response = requests.get(url, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"Screenshot failed: {response.status_code}")
        data = response.json()
        screenshot_base64 = data.get("value", "")
        return base64.b64decode(screenshot_base64)

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
        """处理返回（iOS 通常用 swipe from left edge）"""
        # iOS 没有物理返回键，尝试使用 accessibility 动作
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
        bundle_id = action.get("app") or action.get("bundle_id")
        if not bundle_id:
            return ActionResult(False, False, "No bundle_id specified")

        success = self.launch_app(bundle_id)
        if success:
            return ActionResult(True, False)
        return ActionResult(False, False, f"App not found: {bundle_id}")

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
        # WDA 2.0+ 使用 touch/perform
        payload = {
            "actions": [
                {"action": "press", "options": {"x": x, "y": y}},
                {"action": "wait", "options": {"ms": 50}},
                {"action": "release", "options": {}},
            ]
        }

        try:
            session_id = asyncio.get_event_loop().run_until_complete(self._ensure_session())
            url = f"{self.wda_url}/session/{session_id}/wda/touch/multi/perform"
            response = requests.post(url, json=payload, timeout=10)

            if response.status_code != 200:
                # 回退到 tap 端点
                url = f"{self.wda_url}/wda/tap/0"
                response = requests.post(url, json={"x": x, "y": y}, timeout=10)
        except Exception:
            pass

        time.sleep(DEFAULT_TAP_DELAY)

    def double_tap(self, x: int, y: int) -> None:
        """双击"""
        self.tap(x, y)
        time.sleep(0.05)
        self.tap(x, y)
        time.sleep(DEFAULT_TAP_DELAY)

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> None:
        """长按"""
        payload = {
            "actions": [
                {"action": "press", "options": {"x": x, "y": y}},
                {"action": "wait", "options": {"ms": duration_ms}},
                {"action": "release", "options": {}},
            ]
        }

        try:
            session_id = asyncio.get_event_loop().run_until_complete(self._ensure_session())
            url = f"{self.wda_url}/session/{session_id}/wda/touch/multi/perform"
            response = requests.post(url, json=payload, timeout=10)
        except Exception:
            pass

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
        # WDA 使用 performActions 进行滑动
        payload = {
            "actions": [
                {"action": "press", "options": {"x": start_x, "y": start_y}},
                {"action": "wait", "options": {"ms": duration_ms}},
                {"action": "moveTo", "options": {"x": end_x, "y": end_y}},
                {"action": "release", "options": {}},
            ]
        }

        try:
            session_id = asyncio.get_event_loop().run_until_complete(self._ensure_session())
            url = f"{self.wda_url}/session/{session_id}/wda/touch/multi/perform"
            response = requests.post(url, json=payload, timeout=10)

            if response.status_code != 200:
                # 回退到 swipe 端点
                url = f"{self.wda_url}/wda/touch/perform"
                payload = {
                    "actions": [
                        {"action": "press", "options": {"x": start_x, "y": start_y}},
                        {"action": "moveTo", "options": {"x": end_x, "y": end_y}},
                        {"action": "release", "options": {}},
                    ]
                }
                requests.post(url, json=payload, timeout=10)
        except Exception:
            pass

        time.sleep(DEFAULT_SWIPE_DELAY)

    def back(self) -> None:
        """返回键（iOS 模拟器支持）"""
        try:
            session_id = asyncio.get_event_loop().run_until_complete(self._ensure_session())
            url = f"{self.wda_url}/session/{session_id}/wda/goBack"
            response = requests.post(url, timeout=5)
        except Exception:
            pass

        time.sleep(DEFAULT_BACK_DELAY)

    def home(self) -> None:
        """Home 键"""
        try:
            session_id = asyncio.get_event_loop().run_until_complete(self._ensure_session())
            url = f"{self.wda_url}/session/{session_id}/wda/homescreen"
            response = requests.post(url, timeout=5)
        except Exception:
            pass

        time.sleep(DEFAULT_HOME_DELAY)

    def launch_app(self, bundle_id: str) -> bool:
        """启动应用"""
        try:
            session_id = asyncio.get_event_loop().run_until_complete(self._ensure_session())
            url = f"{self.wda_url}/session/{session_id}/appium/app/launch"
            payload = {"bundleId": bundle_id}
            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                time.sleep(DEFAULT_LAUNCH_DELAY)
                return True

            # 尝试 alternative 端点
            url = f"{self.wda_url}/session/{session_id}/app/launch"
            response = requests.post(url, json={"bundleId": bundle_id}, timeout=10)
            return response.status_code == 200

        except Exception:
            return False

    def type_text(self, text: str) -> None:
        """输入文本"""
        import urllib.parse

        try:
            session_id = asyncio.get_event_loop().run_until_complete(self._ensure_session())
            url = f"{self.wda_url}/session/{session_id}/wda/keys"
            payload = {"value": list(text)}  # WDA 需要字符数组
            response = requests.post(url, json=payload, timeout=10)
        except Exception:
            pass
