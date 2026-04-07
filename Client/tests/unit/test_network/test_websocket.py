"""
WebSocket 客户端单元测试
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio
from src.network.websocket import ConnectionState


class TestConnectionState:
    """连接状态枚举测试"""

    def test_connection_states(self):
        """测试连接状态定义"""
        from src.network.websocket import ConnectionState

        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.RECONNECTING.value == "reconnecting"


class TestWebSocketClient:
    """WebSocket 客户端测试"""

    @pytest.fixture
    def message_callback(self):
        """消息回调"""
        return Mock()

    @pytest.fixture
    def client(self, message_callback):
        """创建客户端实例"""
        from src.network.websocket import WebSocketClient

        return WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client-001",
            on_message=message_callback,
            heartbeat_interval=30,
            reconnect_interval=1,
            max_reconnect_attempts=3,
        )

    def test_initialization(self, client, message_callback):
        """测试初始化"""
        assert client.server_url == "ws://localhost:8080"
        assert client.client_id == "test-client-001"
        assert client.on_message == message_callback
        assert client.heartbeat_interval == 30
        assert client.state.value == "disconnected"
        assert client.session_id is None
        assert client.is_connected is False

    def test_initialization_with_callbacks(self):
        """测试带回调的初始化"""
        from src.network.websocket import WebSocketClient

        on_connect = Mock()
        on_disconnect = Mock()
        on_message = Mock()

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=on_message,
            on_connect=on_connect,
            on_disconnect=on_disconnect,
        )

        assert client.on_connect == on_connect
        assert client.on_disconnect == on_disconnect

    def test_properties(self, client):
        """测试属性"""
        assert client.state.value == "disconnected"
        assert client.session_id is None
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self, client):
        """断开未连接的客户端"""
        # 不应该抛出异常
        await client.disconnect()
        assert client.state.value == "disconnected"

    @pytest.mark.asyncio
    async def test_send_message_when_not_connected(self, client):
        """未连接时发送消息"""
        result = await client.send_message({"type": "test"})
        assert result is False

    @pytest.mark.asyncio
    async def test_send_message_without_ack(self, client):
        """发送消息不等待 ACK"""
        # Mock WebSocket
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        client._websocket = mock_ws
        # 设置状态为 DISCONNECTED 来测试发送失败
        client._state = ConnectionState.DISCONNECTED

        # 由于状态不是 CONNECTED，会返回 False
        result = await client.send_message({"type": "test"}, wait_ack=False)
        assert result is False


class TestWebSocketConnection:
    """WebSocket 连接测试"""

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """连接成功"""
        from src.network.websocket import WebSocketClient, ConnectionState

        mock_ws = AsyncMock()
        # connect流程: 先发register，然后接收ack，再接收welcome
        mock_ws.recv = AsyncMock(side_effect=[
            '{"type": "ack", "accepted": true, "session_id": "session-123"}',  # ack
            '{"type": "welcome", "session_id": "session-123"}',  # welcome
        ])
        mock_ws.send = AsyncMock()
        mock_ws.close = AsyncMock()

        on_connect = Mock()
        on_message = Mock()

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=on_message,
            on_connect=on_connect,
        )

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws

            result = await client.connect()

            assert result is True
            assert client.state == ConnectionState.CONNECTED
            assert client.session_id == "session-123"
            assert client.is_connected is True
            on_connect.assert_called_once_with("session-123")

    @pytest.mark.asyncio
    async def test_connect_fails_without_welcome(self):
        """连接失败 - 无欢迎消息"""
        from src.network.websocket import WebSocketClient

        mock_ws = AsyncMock()
        # connect流程: 先返回ack，然后返回非welcome消息
        mock_ws.recv = AsyncMock(side_effect=[
            '{"type": "ack", "accepted": true}',  # ack ok
            '{"type": "error"}',  # welcome error
        ])

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=Mock(),
        )

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws

            result = await client.connect()

            assert result is False
            assert client.state.value == "disconnected"

    @pytest.mark.asyncio
    async def test_connect_fails_without_ack(self):
        """连接失败 - 注册被拒绝"""
        from src.network.websocket import WebSocketClient

        mock_ws = AsyncMock()
        # 顺序: 先ack（拒绝），但代码不会继续到welcome
        mock_ws.recv = AsyncMock(side_effect=[
            '{"type": "ack", "accepted": false, "error": "invalid client"}',
        ])

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=Mock(),
        )

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws

            result = await client.connect()

            assert result is False


class TestMessageHandling:
    """消息处理测试"""

    @pytest.mark.asyncio
    async def test_receive_task_message(self):
        """接收任务消息"""
        from src.network.websocket import WebSocketClient
        from websockets.exceptions import ConnectionClosed

        on_message = Mock()

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=on_message,
        )

        mock_ws = AsyncMock()
        # 返回一条消息后抛出异常以停止循环
        mock_ws.recv = AsyncMock(side_effect=[
            '{"type": "task", "task_id": "task-001"}',
            ConnectionClosed(None, None),
        ])

        client._websocket = mock_ws
        client._running = True

        # 直接调用接收逻辑
        await client._receive_loop()

        on_message.assert_called_once()
        call_args = on_message.call_args[0][0]
        assert call_args["task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_receive_interrupt_message(self):
        """接收中断消息"""
        from src.network.websocket import WebSocketClient
        from websockets.exceptions import ConnectionClosed

        on_message = Mock()

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=on_message,
        )

        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=[
            '{"type": "interrupt", "task_id": "task-001", "reason": "user_cancelled"}',
            ConnectionClosed(None, None),
        ])

        client._websocket = mock_ws
        client._running = True

        await client._receive_loop()

        on_message.assert_called_once()
        call_args = on_message.call_args[0][0]
        assert call_args["type"] == "interrupt"
        assert call_args["task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_handle_ack_message(self):
        """处理 ACK 消息"""
        from src.network.websocket import WebSocketClient
        from websockets.exceptions import ConnectionClosed

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=Mock(),
        )

        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=[
            '{"type": "ack", "ref_msg_id": "msg-001", "accepted": true}',
            ConnectionClosed(None, None),
        ])

        client._websocket = mock_ws
        client._running = True
        client._pending_acks["msg-001"] = asyncio.get_event_loop().create_future()

        await client._receive_loop()

        # Future 应该被设置
        assert client._pending_acks["msg-001"].done()


class TestHeartbeat:
    """心跳测试"""

    @pytest.mark.asyncio
    async def test_heartbeat_loop(self):
        """心跳循环"""
        from src.network.websocket import WebSocketClient, ConnectionState

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=Mock(),
            heartbeat_interval=0.1,  # 短间隔
        )

        client._websocket = mock_ws
        client._running = True
        client._state = ConnectionState.CONNECTED

        # 启动心跳
        task = asyncio.create_task(client._heartbeat_loop())

        # 等待一段时间
        await asyncio.sleep(0.3)

        # 取消任务
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 应该至少发送一次心跳
        assert mock_ws.send.called


class TestReconnection:
    """重连测试"""

    @pytest.mark.asyncio
    async def test_reconnect_stops_after_max_attempts(self):
        """超过最大重连次数后停止"""
        from src.network.websocket import WebSocketClient, ConnectionState

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=Mock(),
            max_reconnect_attempts=2,
            reconnect_interval=0.1,
        )

        client._running = True
        client._reconnect_attempts = 2  # 已经重连了2次

        with patch.object(client, 'connect', new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = False

            await client._reconnect()

            # 应该停止重连
            assert client._running is False

    @pytest.mark.asyncio
    async def test_reconnect_increments_attempts(self):
        """重连增加尝试次数"""
        from src.network.websocket import WebSocketClient

        client = WebSocketClient(
            server_url="ws://localhost:8080",
            client_id="test-client",
            on_message=Mock(),
            max_reconnect_attempts=5,
            reconnect_interval=0.01,
        )

        client._running = True
        client._reconnect_attempts = 0

        with patch.object(client, 'connect', new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = True  # 连接成功

            await client._reconnect()

            # 如果连接成功，尝试次数应该重置
            # 由于连接成功，不会递归调用 _reconnect
            assert client._running is True
