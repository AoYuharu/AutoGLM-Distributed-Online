"""
WebSocket 客户端

参照 DESIGN.md 中的网络层设计
"""
import asyncio
import json
import logging
import random
import time
from typing import Callable, Optional
from dataclasses import asdict
from enum import Enum
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """连接状态"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class WebSocketClient:
    """
    WebSocket 客户端

    处理与服务器的 WebSocket 连接，支持：
    - 自动重连（指数退避 + Jitter）
    - 消息确认
    - 重连后状态同步
    """

    # 指数退避配置
    RECONNECT_BASE_DELAY = 1.0  # 基础延迟（秒）
    RECONNECT_MAX_DELAY = 60.0  # 最大延迟（秒）

    def __init__(
        self,
        server_url: str,
        client_id: str,
        device_id: str,
        on_message: Callable[[dict], None],
        on_connect: Optional[Callable[[str], None]] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
        on_reconnect_failed: Optional[Callable[[], None]] = None,
        max_reconnect_attempts: int = 10,
    ):
        """
        初始化 WebSocket 客户端

        Args:
            server_url: 服务器 URL
            client_id: 客户端 ID（机器哈希）
            device_id: 设备 ID（真实设备标识，如 ADB 设备序列号）
            on_message: 消息回调
            on_connect: 连接成功回调 (session_id)
            on_disconnect: 断开连接回调
            on_reconnect_failed: 重连失败回调（达到最大重试次数）
            max_reconnect_attempts: 最大重连次数
        """
        self.server_url = server_url
        self.client_id = client_id
        self.device_id = device_id
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_reconnect_failed = on_reconnect_failed
        self.max_reconnect_attempts = max_reconnect_attempts

        self._state = ConnectionState.DISCONNECTED
        self._websocket = None
        self._receive_task: Optional[asyncio.Task] = None
        self._running = False
        self._session_id: Optional[str] = None
        self._pending_acks: dict[str, asyncio.Future] = {}
        self._reconnect_attempts = 0

    async def connect(self) -> bool:
        """
        连接到服务器

        Returns:
            是否连接成功
        """
        # 构建 WebSocket URL（Server 端点在 /ws）
        ws_url = self.server_url.rstrip("/")
        if not ws_url.endswith("/ws"):
            ws_url += "/ws"
        ws_url += f"?client_id={self.client_id}&device_id={self.device_id}"

        logger.info(f"[connect] Connecting to {ws_url} as {self.client_id}")
        start_time = time.time()

        try:
            self._state = ConnectionState.CONNECTING

            # 动态导入以支持可选依赖
            import websockets
            from websockets.exceptions import ConnectionClosed

            self._websocket = await websockets.connect(ws_url)

            # 接收连接确认（Server 会直接发送 connected 消息）
            ack_data = await self._websocket.recv()
            ack = json.loads(ack_data)
            logger.debug(f"[connect] Received ack: {ack}")

            if ack.get("type") != "connected" or not ack.get("accepted"):
                logger.error(f"[connect] Connection failed: {ack}")
                self._state = ConnectionState.DISCONNECTED
                return False

            self._session_id = ack.get("device_id", self.client_id)

            self._state = ConnectionState.CONNECTED
            self._running = True
            self._reconnect_attempts = 0

            # 启动接收任务
            self._receive_task = asyncio.create_task(self._receive_loop())

            if self.on_connect:
                self.on_connect(self._session_id)

            elapsed = (time.time() - start_time) * 1000
            logger.info(f"[connect] Connected to {self.server_url}, session: {self._session_id} ({elapsed:.1f}ms)")
            return True

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"[connect] Connection failed to {self.server_url}: {e} ({elapsed:.1f}ms)")
            self._state = ConnectionState.DISCONNECTED
            return False

    async def disconnect(self, reason: str = "user_requested") -> None:
        """断开连接"""
        logger.info(f"[disconnect] Disconnecting from server, reason: {reason}")
        self._running = False

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

        self._state = ConnectionState.DISCONNECTED

        if self.on_disconnect:
            self.on_disconnect()

        logger.info(f"[disconnect] Disconnected from server (reason: {reason})")

    async def send_message(
        self,
        message: dict,
        wait_ack: bool = True,
        timeout: float = 10.0
    ) -> bool:
        """
        发送消息

        Args:
            message: 消息字典
            wait_ack: 是否等待 ACK
            timeout: 等待 ACK 超时（秒）

        Returns:
            是否发送成功
        """
        if self._state != ConnectionState.CONNECTED or not self._websocket:
            logger.warning(f"[send_message] Cannot send message, not connected (state: {self._state})")
            return False

        start_time = time.time()
        try:
            # 添加 msg_id 如果没有
            if "msg_id" not in message:
                message["msg_id"] = str(uuid.uuid4())

            # 添加时间戳
            if "timestamp" not in message:
                message["timestamp"] = datetime.now().isoformat() + "Z"

            msg_type = message.get("type", "unknown")
            logger.debug(f"[send_message] Sending message: type={msg_type}, msg_id={message['msg_id']}")

            await self._websocket.send(json.dumps(message))

            # 网络消息归档日志
            logger.info(f"[network_outgoing] WebSocket to {self.server_url}: type={msg_type}, msg_id={message['msg_id']}")

            if wait_ack and message.get("msg_id"):
                # 等待 ACK
                future = asyncio.get_event_loop().create_future()
                self._pending_acks[message["msg_id"]] = future

                try:
                    ack = await asyncio.wait_for(future, timeout=timeout)
                    elapsed = (time.time() - start_time) * 1000
                    logger.debug(f"[send_message] ACK received for {message['msg_id']}: {ack.get('accepted')} ({elapsed:.1f}ms)")
                    return ack.get("accepted", False)
                except asyncio.TimeoutError:
                    elapsed = (time.time() - start_time) * 1000
                    logger.warning(f"[send_message] ACK timeout for message {message['msg_id']} ({elapsed:.1f}ms)")
                    return False
                finally:
                    self._pending_acks.pop(message["msg_id"], None)

            return True

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"[send_message] Send message failed: {e} ({elapsed:.1f}ms)")
            return False

    async def _receive_loop(self) -> None:
        """接收消息循环"""
        import websockets
        from websockets.exceptions import ConnectionClosed

        logger.debug("[_receive_loop] Receive loop started")
        while self._running:
            try:
                message = await self._websocket.recv()
                data = json.loads(message)
                msg_type = data.get("type", "unknown")
                msg_id = data.get("msg_id", "N/A")

                logger.debug(f"[_receive_loop] Received message: type={msg_type}, msg_id={msg_id}")

                if msg_type == "ack":
                    # 处理 ACK
                    ref_id = data.get("ref_msg_id")
                    logger.debug(f"[_receive_loop] ACK for ref_id={ref_id}")
                    if ref_id in self._pending_acks:
                        self._pending_acks[ref_id].set_result(data)
                else:
                    # 其他消息传递给回调
                    logger.debug(f"[_receive_loop] Passing message to handler: type={msg_type}")
                    self.on_message(data)

            except asyncio.CancelledError:
                break
            except ConnectionClosed:
                logger.warning("[_receive_loop] WebSocket closed")
                break
            except json.JSONDecodeError:
                logger.error(f"[_receive_loop] Invalid JSON received: {message}")
            except Exception as e:
                logger.error(f"[_receive_loop] Receive error: {e}")

        if self._running:
            logger.info("[_receive_loop] Connection lost, initiating reconnect")
            await self._reconnect()

    async def _reconnect(self) -> None:
        """重连（指数退避 + Jitter）"""
        self._state = ConnectionState.RECONNECTING
        self._reconnect_attempts += 1

        if self._reconnect_attempts > self.max_reconnect_attempts:
            logger.error(f"[_reconnect] Max reconnect attempts ({self.max_reconnect_attempts}) reached, giving up")
            self._running = False
            # 通知上层重连失败
            if self.on_reconnect_failed:
                self.on_reconnect_failed()
            return

        # 计算延迟：min(base * 2^attempt + jitter, max_delay)
        delay = min(
            self.RECONNECT_BASE_DELAY * (2 ** (self._reconnect_attempts - 1)),
            self.RECONNECT_MAX_DELAY
        )
        # 添加 jitter (0-1秒)
        jitter = random.uniform(0, 1)
        total_delay = delay + jitter

        logger.info(f"[_reconnect] Reconnecting... attempt {self._reconnect_attempts}/{self.max_reconnect_attempts}, delay={total_delay:.2f}s")

        await asyncio.sleep(total_delay)

        if self._running:
            success = await self.connect()
            if success:
                self._reconnect_attempts = 0
                logger.info("[_reconnect] Reconnected successfully")
            elif self._running:
                # 重连失败，递归重试
                await self._reconnect()

    @property
    def state(self) -> ConnectionState:
        """获取连接状态"""
        return self._state

    @property
    def session_id(self) -> Optional[str]:
        """获取会话 ID"""
        return self._session_id

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._state == ConnectionState.CONNECTED
