"""
Distributed Client 主程序

分布式手机自动化客户端入口
精简版：只处理 action_cmd via WebSocket，device_status/observe_result via HTTP
"""
import asyncio
import argparse
import base64
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from src.config import load_client_runtime_config, merge_cli_overrides
from src.network.websocket import WebSocketClient, ConnectionState
from src.network.http_client import HttpClient
from src.polling.manager import PollingManager
from src.polling.factory import PlatformType
from src.adapters import ADBAdapter, HDCAdapter, WDAAdapter, DeviceAdapterBase
from src.adapters.base import ActionResult
from src.screenshot import ScreenshotManager, ScreenshotConfig
from src.logging import ClientLogger, LogConfig
from src.network.messages import (
    AckMessage,
    AckErrorCode,
)


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("client")


class DistributedClient:
    """
    分布式客户端主类

    精简架构：
    - WebSocket: 只接收 action_cmd，只发送 ack
    - HTTP: 发送 device_status, observe_result
    """

    def __init__(
        self,
        server_url: str,
        client_id: Optional[str] = None,
        enable_adb: bool = True,
        enable_hdc: bool = False,
        enable_wda: bool = False,
        log_level: str = "INFO",
        http_base_url: Optional[str] = None,
        polling_interval: float = 3.0,
        adb_binary: str = "adb",
        hdc_binary: str = "hdc",
        wda_url: str = "http://localhost:8100",
        wda_session_timeout: int = 300,
        ws_max_reconnect_attempts: int = 10,
        ws_reconnect_base_delay: float = 1.0,
        ws_reconnect_max_delay: float = 60.0,
        ws_send_ack_timeout: float = 10.0,
        http_timeout: float = 30.0,
        http_observe_retry_attempts: int = 1,
    ):
        """
        初始化分布式客户端

        Args:
            server_url: 服务器 WebSocket URL
            client_id: 客户端 ID（不指定则自动生成）
            enable_adb: 是否启用 ADB
            enable_hdc: 是否启用 HDC
            enable_wda: 是否启用 WDA
            log_level: 日志级别
        """
        self.client_id = client_id or self._generate_client_id()
        self.server_url = server_url
        self.server_base_url = (
            http_base_url
            or server_url.replace("ws://", "http://")
            .replace("wss://", "https://")
            .split("/ws")[0]
            .split("/ws/")[0]
        )
        self.polling_interval = polling_interval
        self.adb_binary = adb_binary
        self.hdc_binary = hdc_binary
        self.wda_url = wda_url
        self.wda_session_timeout = wda_session_timeout
        self.ws_max_reconnect_attempts = ws_max_reconnect_attempts
        self.ws_reconnect_base_delay = ws_reconnect_base_delay
        self.ws_reconnect_max_delay = ws_reconnect_max_delay
        self.ws_send_ack_timeout = ws_send_ack_timeout
        self.http_timeout = http_timeout
        self.http_observe_retry_attempts = http_observe_retry_attempts

        # 初始化日志
        log_config = LogConfig(level=log_level)
        self.logger = ClientLogger(
            config=log_config,
            client_id=self.client_id,
        )

        # 设备适配器字典
        self.device_adapters: dict[str, DeviceAdapterBase] = {}

        # 轮询管理器
        self.polling_manager = PollingManager(
            on_device_found=self._on_device_found,
            on_device_lost=self._on_device_lost,
            on_polling_cycle_complete=self._on_polling_cycle_complete,
            interval=self.polling_interval,
            adb_binary=self.adb_binary,
            hdc_binary=self.hdc_binary,
            wda_url=self.wda_url,
        )

        # WebSocket 客户端
        self.ws_client: Optional[WebSocketClient] = None

        # HTTP 客户端
        self.http_client: Optional[HttpClient] = None

        # 已执行的 action 版本（用于幂等性）
        self._executed_versions: set[str] = set()

        # 运行状态
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 注册信号处理器
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # 启用平台轮询
        if enable_adb:
            self.polling_manager.enable_platform(PlatformType.ADB, adb_path=self.adb_binary)
        if enable_hdc:
            self.polling_manager.enable_platform(PlatformType.HDC, hdc_path=self.hdc_binary)
        if enable_wda:
            self.polling_manager.enable_platform(PlatformType.WDA, wda_url=self.wda_url)

    def _generate_client_id(self) -> str:
        """生成客户端 ID"""
        import hashlib
        import platform
        import uuid

        # 使用硬件特征生成唯一 ID
        raw = f"{platform.node()}-{uuid.getnode()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()

    def _signal_handler(self, signum, frame):
        """信号处理器"""
        logger.info(f"Received signal {signum}, shutting down...")
        asyncio.create_task(self.stop())

    def _schedule_async(self, coro):
        """在线程中安全地调度异步任务"""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            import threading
            def run_async():
                asyncio.run(coro)
            threading.Thread(target=run_async, daemon=True).start()

    async def _reconnect_websocket_with_device_id(self, device_id: str) -> None:
        """使用真实的 device_id 重新连接 WebSocket"""
        if not self.ws_client:
            return

        logger.info(f"Reconnecting WebSocket with device_id={device_id}")

        # 断开旧连接
        await self.ws_client.disconnect()

        # 创建新的 WebSocket 客户端，使用真实的 device_id
        self.ws_client = WebSocketClient(
            server_url=self.server_url,
            client_id=self.client_id,
            device_id=device_id,
            on_message=self._on_ws_message,
            on_connect=self._on_ws_connect,
            on_disconnect=self._on_ws_disconnect,
            max_reconnect_attempts=self.ws_max_reconnect_attempts,
            reconnect_base_delay=self.ws_reconnect_base_delay,
            reconnect_max_delay=self.ws_reconnect_max_delay,
            send_ack_timeout=self.ws_send_ack_timeout,
        )

        # 连接
        connected = await self.ws_client.connect()
        if connected:
            logger.info(f"WebSocket reconnected with device_id={device_id}")
        else:
            logger.error(f"Failed to reconnect WebSocket with device_id={device_id}")

    # === 设备管理 ===

    def _on_device_found(self, device_id: str, device_info: dict) -> None:
        """设备发现回调"""
        platform = device_info.get("platform", "android")
        # 统一平台命名: adb -> android
        if platform == "adb":
            platform = "android"

        # 创建适配器
        if platform == "android":
            adapter = ADBAdapter(device_id=device_id, adb_path=self.adb_binary)
        elif platform == "harmonyos":
            adapter = HDCAdapter(device_id=device_id, hdc_path=self.hdc_binary)
        elif platform == "ios":
            wda_url = device_info.get("wda_url", self.wda_url)
            adapter = WDAAdapter(
                device_id=device_id,
                wda_url=wda_url,
                session_timeout=self.wda_session_timeout,
            )
        else:
            logger.warning(f"Unknown platform: {platform}")
            return

        self.device_adapters[device_id] = adapter
        logger.info(f"Device found: {device_id} ({platform})")
        self.logger.log_device_connected(device_id, platform)

        # 如果 WebSocket 使用的 device_id 是 client_id（而非真实设备ID），则重新连接
        # 这发生在首次启动时，WebSocket 在设备被发现前就连接了
        if self.ws_client and self.ws_client.device_id == self.client_id:
            logger.info(f"WebSocket using fallback device_id={self.client_id}, reconnecting with real device_id={device_id}")
            self._schedule_async(self._reconnect_websocket_with_device_id(device_id))

    def _on_device_lost(self, device_id: str) -> None:
        """设备丢失回调"""
        if device_id in self.device_adapters:
            del self.device_adapters[device_id]

        logger.info(f"Device lost: {device_id}")
        self.logger.log_device_disconnected(device_id)

        # 向 Server 报告设备离线
        self._schedule_async(self._report_device_offline(device_id))

    async def _report_device_offline(self, device_id: str) -> None:
        """报告设备离线到 Server"""
        if not self.http_client:
            return
        try:
            await self.http_client.send_device_offline(device_id)
            logger.info(f"Reported device offline to server: {device_id}")
        except Exception as e:
            logger.error(f"Failed to report device offline: {e}")

    def _on_polling_cycle_complete(self, temp_devices: dict, previous_devices: dict) -> None:
        """轮询周期完成回调 - 对比设备变化并全量上报"""
        # 检查设备变化
        added_devices = set(temp_devices.keys()) - set(previous_devices.keys())
        removed_devices = set(previous_devices.keys()) - set(temp_devices.keys())

        if added_devices or removed_devices:
            logger.debug(f"Device state changed: added={list(added_devices)}, removed={list(removed_devices)}")

        # 触发全量上报
        self._schedule_async(self._report_device_status(temp_devices))

    # === WebSocket 消息处理 ===

    def _on_ws_connect(self, session_id: str) -> None:
        """WebSocket 连接成功"""
        logger.info(f"Connected to server, session: {session_id}")
        self.logger.log_client_connected(session_id)

    def _on_ws_disconnect(self) -> None:
        """WebSocket 断开连接"""
        logger.warning("Disconnected from server")
        self.logger.log_client_disconnected()

    def _on_ws_message(self, message: dict) -> None:
        """WebSocket 消息回调 - 处理 action_cmd 和 request_screenshot"""
        msg_type = message.get("type")

        if msg_type == "action_cmd":
            asyncio.create_task(self._handle_action_cmd(message))
        elif msg_type == "request_screenshot":
            asyncio.create_task(self._handle_request_screenshot(message))
        else:
            logger.debug(f"Ignoring WebSocket message: {msg_type}")

    async def _handle_action_cmd(self, message: dict) -> None:
        """处理 action_cmd - 执行 action 并发送 observe_result"""
        try:
            msg_id = message.get("msg_id", "")
            payload = message.get("payload", {})
            device_id = payload.get("device_id", "")
            action = payload.get("action", {})
            task_id = payload.get("task_id", "")
            step_number = payload.get("step_number", 1)
            version = message.get("version", "")
            round_version = int(version) if str(version).isdigit() else None

            if round_version is None:
                logger.warning(f"Ignoring action_cmd without numeric version: task_id={task_id}, device_id={device_id}, step={step_number}, version={version}")
                return

            logger.info(
                f"Received action_cmd: task_id={task_id}, device_id={device_id}, step={step_number}, "
                f"version={round_version}, action={action.get('action')}"
            )

            version_key = f"{device_id}:{task_id}:{round_version}"

            # 幂等性检查：同一任务内已执行的 version 直接返回 ack（防止网络重试导致的重复）
            if version_key in self._executed_versions:
                logger.info(
                    f"Duplicate action_cmd ignored: task_id={task_id}, device_id={device_id}, "
                    f"step={step_number}, version={round_version}"
                )
                await self._send_ack(
                    ref_msg_id=msg_id,
                    accepted=True,
                    device_id=device_id,
                    version=round_version,
                )
                return

            # 检查设备是否存在
            if device_id not in self.device_adapters:
                await self._send_ack(
                    ref_msg_id=msg_id,
                    accepted=False,
                    device_id=device_id,
                    error="Device not found",
                    error_code=AckErrorCode.DEVICE_OFFLINE.value,
                    version=round_version,
                )
                return

            # 立即发送 ack
            await self._send_ack(
                ref_msg_id=msg_id,
                accepted=True,
                device_id=device_id,
                version=round_version,
            )
            logger.info(
                f"ACK sent: ref_msg_id={msg_id}, task_id={task_id}, device_id={device_id}, "
                f"step={step_number}, version={round_version}, accepted=True"
            )

            # 记录版本
            self._executed_versions.add(version_key)

            # 防止内存膨胀，定期清理旧版本记录
            if len(self._executed_versions) > 1000:
                self._executed_versions = set(list(self._executed_versions)[-500:])
                logger.debug(f"Cleaned up _executed_versions, new size={len(self._executed_versions)}")

            logger.info(f"[DEBUG] Step 1/4 - Starting action execution: task_id={task_id}, device_id={device_id}, step={step_number}, version={round_version}, action={action}")

            adapter = self.device_adapters[device_id]
            result, screenshot, screenshot_error = await self._execute_action_with_observe_capture(
                adapter=adapter,
                action=action,
                task_id=task_id,
                device_id=device_id,
                step_number=step_number,
                round_version=round_version,
            )

            observe_payload = self._build_observe_payload(
                result=result,
                screenshot=screenshot,
                screenshot_error=screenshot_error,
            )

            # 发送 observe_result
            try:
                logger.info(f"[DEBUG] Step 4/4 - Calling send_observe_result()...")
                await self.send_observe_result(
                    task_id=task_id,
                    device_id=device_id,
                    step_number=step_number,
                    screenshot=base64.b64encode(observe_payload["screenshot"]).decode() if observe_payload["screenshot"] else None,
                    result=observe_payload["result"],
                    success=observe_payload["success"],
                    error=observe_payload["error"],
                    version=round_version,
                )
                logger.info(f"[DEBUG] Step 4/4 - send_observe_result() DONE")
            except Exception as e:
                logger.error(f"[DEBUG] Step 4/4 - send_observe_result() EXCEPTION: {e}")
                raise

            logger.info(
                f"Action execution finished: task_id={task_id}, device_id={device_id}, "
                f"step={step_number}, version={round_version}, result={observe_payload['result'][:200]}"
            )

        except Exception as e:
            logger.error(f"Failed to handle action_cmd: {e}")

    async def _execute_action_with_observe_capture(
        self,
        adapter: DeviceAdapterBase,
        action: dict,
        task_id: str,
        device_id: str,
        step_number: int,
        round_version: int,
    ) -> tuple[ActionResult, Optional[bytes], Optional[str]]:
        """Execute an action and capture screenshot best-effort for observe_result."""
        try:
            logger.info(f"[DEBUG] Step 2/4 - adapter.execute_action() starting...")
            result = adapter.execute_action(action)
            logger.info(f"[DEBUG] Step 2/4 - adapter.execute_action() DONE: result.success={result.success}, result.message={result.message}")
        except Exception as e:
            logger.error(f"[DEBUG] Step 2/4 - adapter.execute_action() EXCEPTION: {e}")
            result = ActionResult(
                success=False,
                should_finish=False,
                message=f"Action failed: {e}",
            )

        # 等待1.5秒让界面稳定后再截图
        logger.info(f"[DEBUG] Step 2.5/4 - Waiting 1.5s for UI to stabilize...")
        await asyncio.sleep(1.5)
        logger.info(f"[DEBUG] Step 2.5/4 - Wait complete")

        screenshot = None
        screenshot_error = None
        try:
            logger.info(f"[DEBUG] Step 3/4 - adapter.get_screenshot() starting...")
            screenshot = adapter.get_screenshot()
            logger.info(f"[DEBUG] Step 3/4 - adapter.get_screenshot() DONE: has_screenshot={screenshot is not None}, size={len(screenshot) if screenshot else 0}")
        except Exception as e:
            screenshot_error = str(e)
            logger.error(f"[DEBUG] Step 3/4 - adapter.get_screenshot() EXCEPTION: {screenshot_error}")

        return result, screenshot, screenshot_error

    def _result_to_text(self, result: ActionResult) -> str:
        """Convert ActionResult into a compact observe_result text."""
        if result.message:
            return result.message
        return "Action succeeded" if result.success else "Action failed"

    def _merge_result_error_text(self, base_text: str, extra_text: str) -> str:
        """Append extra failure detail to an observe/result text string."""
        if not base_text:
            return extra_text
        if extra_text in base_text:
            return base_text
        return f"{base_text}; {extra_text}"

    def _build_observe_payload(
        self,
        result: ActionResult,
        screenshot: Optional[bytes],
        screenshot_error: Optional[str] = None,
    ) -> dict:
        """Build observe_result payload fields from action result and screenshot state."""
        result_text = self._result_to_text(result)
        error = None if result.success else result.message

        if screenshot_error:
            screenshot_note = f"Screenshot failed: {screenshot_error}"
            result_text = self._merge_result_error_text(result_text, screenshot_note)
            error = self._merge_result_error_text(error or "", screenshot_note)

        return {
            "screenshot": screenshot,
            "result": result_text,
            "success": result.success,
            "error": error,
        }


    async def _handle_request_screenshot(self, message: dict) -> None:
        """处理 request_screenshot - 先 ACK，再截取屏幕并发送 observe_result"""
        payload = message.get("payload", {})
        task_id = payload.get("task_id", "")
        device_id = payload.get("device_id", "")
        step_number = int(payload.get("step_number", 0) or 0)
        phase = payload.get("phase", "observe")
        purpose = payload.get("purpose", "bootstrap")
        ref_msg_id = message.get("msg_id", "")

        logger.info(
            f"Received request_screenshot: task_id={task_id}, device_id={device_id}, "
            f"step_number={step_number}, phase={phase}, purpose={purpose}, ref_msg_id={ref_msg_id}"
        )

        if device_id not in self.device_adapters:
            logger.warning(
                f"Screenshot requested for unknown device: task_id={task_id}, device_id={device_id}"
            )
            if ref_msg_id:
                await self._send_ack(
                    ref_msg_id=ref_msg_id,
                    accepted=False,
                    device_id=device_id,
                    error="Device not found",
                    error_code=AckErrorCode.DEVICE_OFFLINE.value,
                )
            return

        if ref_msg_id:
            await self._send_ack(
                ref_msg_id=ref_msg_id,
                accepted=True,
                device_id=device_id,
            )
            logger.info(
                f"Bootstrap screenshot ACK sent: task_id={task_id}, device_id={device_id}, ref_msg_id={ref_msg_id}"
            )

        try:
            adapter = self.device_adapters[device_id]
            loop = asyncio.get_running_loop()
            logger.info(
                f"Bootstrap screenshot capture started: task_id={task_id}, device_id={device_id}"
            )
            # Run blocking get_screenshot() in thread pool to avoid blocking the event loop
            screenshot = await loop.run_in_executor(None, adapter.get_screenshot)
            logger.info(
                f"Bootstrap screenshot capture finished: task_id={task_id}, device_id={device_id}, has_screenshot={bool(screenshot)}"
            )

            await self.send_observe_result(
                task_id=task_id,
                device_id=device_id,
                step_number=step_number,
                screenshot=base64.b64encode(screenshot).decode() if screenshot else None,
                result="screenshot_captured",
                success=True,
            )
            logger.info(
                f"Bootstrap screenshot sent successfully: task_id={task_id}, device_id={device_id}"
            )
        except Exception as exc:
            logger.exception(
                f"Failed to handle request_screenshot: task_id={task_id}, device_id={device_id}"
            )
            await self.send_observe_result(
                task_id=task_id,
                device_id=device_id,
                step_number=step_number,
                screenshot=None,
                result=f"screenshot_capture_failed: {exc}",
                success=False,
                error=str(exc),
            )

    # === 消息发送 ===

    async def _send_ack(
        self,
        ref_msg_id: str,
        accepted: bool,
        device_id: str = "",
        error: Optional[str] = None,
        error_code: Optional[int] = None,
        version: Optional[int] = None,
    ) -> None:
        """发送 ACK via WebSocket"""
        if self.ws_client and self.ws_client.is_connected:
            logger.info(
                f"Preparing ACK: ref_msg_id={ref_msg_id}, device_id={device_id}, version={version}, accepted={accepted}"
            )
            ack = AckMessage.create(
                ref_msg_id=ref_msg_id,
                accepted=accepted,
                device_id=device_id,
                error=error,
                error_code=error_code,
            )
            if version is not None:
                ack.version = str(version)
            await self.ws_client.send_message(ack.to_dict(), wait_ack=False)

    async def send_observe_result(
        self,
        task_id: str,
        device_id: str,
        step_number: int,
        screenshot: Optional[str] = None,
        result: str = "",
        success: bool = True,
        error: Optional[str] = None,
        version: Optional[int] = None,
    ) -> None:
        """发送观察结果 via HTTP"""
        if self.http_client:
            logger.info(
                f"Sending observe_result: task_id={task_id}, device_id={device_id}, "
                f"step={step_number}, version={version}, success={success}, has_screenshot={bool(screenshot)}"
            )
            await self.http_client.send_observe_result(
                task_id=task_id,
                device_id=device_id,
                step_number=step_number,
                screenshot=screenshot,
                result=result,
                success=success,
                error=error,
                version=version,
            )

    async def _report_device_status(self, temp_devices: dict = None) -> None:
        """全量上报设备状态 via HTTP"""
        if not self.http_client:
            return

        # 如果没有传入temp_devices，从device_adapters获取
        if temp_devices is None:
            temp_devices = {device_id: {} for device_id in self.device_adapters.keys()}

        devices = []

        # 对每个 temp_devices 中的设备进行 capabilities check
        for device_id, device_info in temp_devices.items():
            adapter = self.device_adapters.get(device_id)

            # 每次都重新检查 capabilities，确保获取最新设备信息
            if adapter:
                try:
                    await adapter.check_capabilities()
                    logger.info(f"Device capabilities checked: {device_id}")
                except Exception as e:
                    logger.error(f"Failed to check capabilities for {device_id}: {e}")
                    logger.info(f"Skipping device {device_id} (capabilities check failed)")
                    continue

            # 构建上报数据
            device_entry = {
                "device_id": device_id,
                "status": "idle",
            }

            # 添加能力信息
            if adapter and adapter.capabilities:
                caps = adapter.capabilities
                device_entry["capabilities"] = caps.to_dict()
                # 提取顶层字段供 Server 使用
                device_entry["platform"] = caps.platform.value if hasattr(caps.platform, 'value') else str(caps.platform)
                device_entry["device_name"] = caps.device_name or "Unknown"
                device_entry["os_version"] = caps.os_version or ""
                device_entry["screen_size"] = list(caps.screen_size) if caps.screen_size else None

            devices.append(device_entry)

        if devices:
            logger.info(f"Reporting {len(devices)} devices to server")
            await self.http_client.send_device_status(devices)

    # === 生命周期 ===

    async def start(self) -> None:
        """启动客户端"""
        self._running = True
        self._loop = asyncio.get_event_loop()

        logger.info(f"Starting Distributed Client: {self.client_id}")
        self.logger.log_client_started()

        # 连接服务器
        # 使用第一个设备 ID 作为 device_id，如果还没有设备则使用 client_id
        # WebSocket 连接时使用第一个发现的设备ID，fallback到 client_id
        first_device_id = list(self.device_adapters.keys())[0] if self.device_adapters else self.client_id
        self.ws_client = WebSocketClient(
            server_url=self.server_url,
            client_id=self.client_id,
            device_id=first_device_id,
            on_message=self._on_ws_message,
            on_connect=self._on_ws_connect,
            on_disconnect=self._on_ws_disconnect,
            max_reconnect_attempts=self.ws_max_reconnect_attempts,
            reconnect_base_delay=self.ws_reconnect_base_delay,
            reconnect_max_delay=self.ws_reconnect_max_delay,
            send_ack_timeout=self.ws_send_ack_timeout,
        )

        # 初始化 HTTP 客户端
        self.http_client = HttpClient(
            base_url=self.server_base_url,
            client_id=self.client_id,
            timeout=self.http_timeout,
            observe_retry_attempts=self.http_observe_retry_attempts,
        )

        connected = await self.ws_client.connect()
        if not connected:
            logger.error("Failed to connect to server, retrying...")
            # Retry connection in background
            for i in range(5):
                await asyncio.sleep(2)
                connected = await self.ws_client.connect()
                if connected:
                    break

        if not connected:
            logger.error("Failed to connect to server after retries")
            # Continue anyway - polling will still work and will reconnect

        # 连接成功后启动设备轮询
        self.polling_manager.start()
        logger.info("Device polling started")

        # 主循环
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """停止客户端"""
        logger.info("Stopping Distributed Client...")
        self._running = False

        # 停止轮询
        self.polling_manager.stop()

        # 断开连接
        if self.http_client:
            await self.http_client.close()

        if self.ws_client:
            await self.ws_client.disconnect()

        # 关闭日志
        self.logger.close()

        logger.info("Distributed Client stopped")


async def main():
    """主函数"""
    # 1. 解析 CLI 参数（全部使用 None 默认值，让 YAML 成为实际默认值）
    parser = argparse.ArgumentParser(description="Distributed Phone Automation Client")
    parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="Server WebSocket URL (overrides config/client.yaml)"
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Client ID (auto-generated if not specified)"
    )
    parser.add_argument(
        "--enable-adb",
        action="store_true",
        default=None,
        dest="enable_adb",
        help="Enable ADB device support"
    )
    parser.add_argument(
        "--disable-adb",
        action="store_false",
        default=None,
        dest="enable_adb",
        help="Disable ADB device support"
    )
    parser.add_argument(
        "--enable-hdc",
        action="store_true",
        default=None,
        help="Enable HDC device support"
    )
    parser.add_argument(
        "--enable-wda",
        action="store_true",
        default=None,
        help="Enable WDA device support"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Log level (overrides config/client.yaml)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config/client.yaml (defaults to auto-discovered)"
    )

    args = parser.parse_args()

    # 2. 加载 YAML 配置
    yaml_config = load_client_runtime_config(config_path=args.config)

    # 3. 应用 CLI 覆盖（CLI 优先级最高）
    merge_cli_overrides(yaml_config, args)

    # 4. 使用最终配置实例化 DistributedClient
    client = DistributedClient(
        server_url=yaml_config.server_ws_url,
        client_id=args.client_id,
        enable_adb=yaml_config.adb_enabled,
        enable_hdc=yaml_config.hdc_enabled,
        enable_wda=yaml_config.wda_enabled,
        log_level=yaml_config.log_level,
        http_base_url=yaml_config.server_http_base_url,
        polling_interval=yaml_config.polling_interval,
        adb_binary=yaml_config.adb_binary,
        hdc_binary=yaml_config.hdc_binary,
        wda_url=yaml_config.wda_url,
        wda_session_timeout=yaml_config.wda_session_timeout,
        ws_max_reconnect_attempts=yaml_config.ws_max_reconnect_attempts,
        ws_reconnect_base_delay=yaml_config.ws_reconnect_base_delay,
        ws_reconnect_max_delay=yaml_config.ws_reconnect_max_delay,
        ws_send_ack_timeout=yaml_config.ws_send_ack_timeout,
        http_timeout=yaml_config.http_timeout,
        http_observe_retry_attempts=yaml_config.http_observe_retry_attempts,
    )

    try:
        await client.start()
    except KeyboardInterrupt:
        await client.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        await client.stop()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
