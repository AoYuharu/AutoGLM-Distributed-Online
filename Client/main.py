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

from src.network.websocket import WebSocketClient, ConnectionState
from src.network.http_client import HttpClient
from src.polling.manager import PollingManager
from src.polling.factory import PlatformType
from src.adapters import ADBAdapter, HDCAdapter, WDAAdapter, DeviceAdapterBase
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
        # HTTP 客户端使用相同主机但不同路径
        self.server_base_url = server_url.replace("ws://", "http://").replace("wss://", "https://").split("/ws")[0].split("/ws/")[0]

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
            self.polling_manager.enable_platform(PlatformType.ADB)
        if enable_hdc:
            self.polling_manager.enable_platform(PlatformType.HDC)
        if enable_wda:
            self.polling_manager.enable_platform(PlatformType.WDA)

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
            adapter = ADBAdapter(device_id=device_id)
        elif platform == "harmonyos":
            adapter = HDCAdapter(device_id=device_id)
        elif platform == "ios":
            wda_url = device_info.get("wda_url", "http://localhost:8100")
            adapter = WDAAdapter(device_id=device_id, wda_url=wda_url)
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

            version_key = f"{device_id}:{round_version}"

            # 幂等性检查：已执行的 version 直接返回 ack
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

            logger.info(f"Executing action: task_id={task_id}, device_id={device_id}, step={step_number}, version={round_version}")

            # 执行 action
            adapter = self.device_adapters[device_id]
            try:
                result = adapter.execute_action(action)
                logger.info(
                    f"Action execution finished: task_id={task_id}, device_id={device_id}, "
                    f"step={step_number}, version={round_version}, result={str(result)[:200]}"
                )
                screenshot = adapter.get_screenshot()

                # 发送 observe_result
                await self.send_observe_result(
                    task_id=task_id,
                    device_id=device_id,
                    step_number=step_number,
                    screenshot=base64.b64encode(screenshot).decode() if screenshot else None,
                    result=str(result),
                    success=result.success,
                    error=None if result.success else result.message,
                    version=round_version,
                )
            except Exception as e:
                logger.error(f"Action execution failed: {e}")
                await self.send_observe_result(
                    task_id=task_id,
                    device_id=device_id,
                    step_number=step_number,
                    success=False,
                    error=str(e),
                    version=round_version,
                )

        except Exception as e:
            logger.error(f"Failed to handle action_cmd: {e}")

    async def _handle_request_screenshot(self, message: dict) -> None:
        """处理 request_screenshot - 截取屏幕并发送 observe_result"""
        try:
            device_id = message.get("device_id", "")
            task_id = message.get("task_id", "")

            if device_id not in self.device_adapters:
                logger.warning(f"Screenshot requested for unknown device: {device_id}")
                return

            adapter = self.device_adapters[device_id]
            screenshot = adapter.get_screenshot()

            await self.send_observe_result(
                task_id=task_id,
                device_id=device_id,
                step_number=0,
                screenshot=base64.b64encode(screenshot).decode() if screenshot else None,
                result="screenshot_captured",
                success=True,
            )
            logger.info(f"Screenshot sent for device={device_id}, task={task_id}")
        except Exception as e:
            logger.error(f"Failed to handle request_screenshot: {e}")

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
        )

        # 初始化 HTTP 客户端
        self.http_client = HttpClient(
            base_url=self.server_base_url,
            client_id=self.client_id,
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
    parser = argparse.ArgumentParser(description="Distributed Phone Automation Client")
    parser.add_argument(
        "--server",
        type=str,
        default="ws://localhost:8080",
        help="Server WebSocket URL"
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
        default=True,
        help="Enable ADB device support"
    )
    parser.add_argument(
        "--enable-hdc",
        action="store_true",
        help="Enable HDC device support"
    )
    parser.add_argument(
        "--enable-wda",
        action="store_true",
        help="Enable WDA device support"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level"
    )

    args = parser.parse_args()

    client = DistributedClient(
        server_url=args.server,
        client_id=args.client_id,
        enable_adb=args.enable_adb,
        enable_hdc=args.enable_hdc,
        enable_wda=args.enable_wda,
        log_level=args.log_level,
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
