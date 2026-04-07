"""
TaskExecutor 单元测试
"""
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from src.executor.task_executor import TaskExecutor, TaskStatus, TaskContext


class TestTaskContext:
    """TaskContext 测试"""

    def test_task_context_creation(self):
        """创建任务上下文"""
        ctx = TaskContext(
            task_id="task_001",
            device_id="device_001",
            instruction="打开微信"
        )
        assert ctx.task_id == "task_001"
        assert ctx.device_id == "device_001"
        assert ctx.instruction == "打开微信"
        assert ctx.status == TaskStatus.PENDING
        assert ctx.current_step == 0

    def test_duration_seconds_calculation(self):
        """时长计算"""
        from datetime import datetime, timedelta
        ctx = TaskContext(
            task_id="task_001",
            device_id="device_001",
            instruction="test",
            start_time=datetime.now() - timedelta(seconds=10),
            end_time=datetime.now()
        )
        assert ctx.duration_seconds >= 9.9  # 允许小误差

    def test_to_dict(self):
        """转换为字典"""
        ctx = TaskContext(
            task_id="task_001",
            device_id="device_001",
            instruction="test"
        )
        d = ctx.to_dict()
        assert d["task_id"] == "task_001"
        assert d["device_id"] == "device_001"
        assert d["status"] == "pending"


class TestTaskExecutor:
    """TaskExecutor 测试"""

    @pytest.fixture
    def mock_adapter(self):
        """Mock 设备适配器"""
        adapter = Mock()
        adapter.get_screenshot.return_value = b"fake_screenshot_data"
        adapter.execute_action.return_value = Mock(
            success=True,
            should_finish=False,
            message=None
        )
        return adapter

    @pytest.fixture
    def mock_model_client(self):
        """Mock 模型客户端"""
        client = AsyncMock()
        client.inference.return_value = {
            "_metadata": "do",
            "action": "Tap",
            "element": {"x": 500, "y": 300}
        }
        return client

    @pytest.fixture
    def mock_screenshot_manager(self):
        """Mock 截图管理器"""
        manager = Mock()
        manager.save.return_value = "/path/to/screenshot.png"
        return manager

    @pytest.mark.asyncio
    async def test_executor_initialization(self, mock_adapter, mock_model_client):
        """执行器初始化"""
        executor = TaskExecutor(
            adapter=mock_adapter,
            model_client=mock_model_client
        )
        assert executor.adapter is mock_adapter
        assert executor.model_client is mock_model_client
        assert executor.current_task is None
        assert not executor.is_running

    @pytest.mark.asyncio
    async def test_execute_task_completes_with_finish_action(
        self, mock_adapter, mock_model_client
    ):
        """任务以 finish 动作完成"""
        # 设置模型返回 finish
        mock_model_client.inference.return_value = {
            "_metadata": "finish",
            "message": "任务完成"
        }

        executor = TaskExecutor(
            adapter=mock_adapter,
            model_client=mock_model_client
        )

        ctx = await executor.execute_task(
            task_id="task_001",
            device_id="device_001",
            instruction="测试任务",
            model_config={},
            max_steps=10
        )

        assert ctx.status == TaskStatus.COMPLETED
        assert ctx.current_step == 0  # finish 不计入步数
        assert len(ctx.actions) == 1
        assert ctx.actions[0]["_metadata"] == "finish"

    @pytest.mark.asyncio
    async def test_execute_task_max_steps_reached(
        self, mock_adapter, mock_model_client
    ):
        """达到最大步数"""
        # 持续返回非 finish 动作
        mock_model_client.inference.return_value = {
            "_metadata": "do",
            "action": "Tap",
            "element": {"x": 500, "y": 300}
        }

        executor = TaskExecutor(
            adapter=mock_adapter,
            model_client=mock_model_client
        )

        ctx = await executor.execute_task(
            task_id="task_001",
            device_id="device_001",
            instruction="测试任务",
            model_config={},
            max_steps=3
        )

        assert ctx.status == TaskStatus.ERROR
        assert ctx.last_error == "Max steps reached"
        assert ctx.current_step == 3

    @pytest.mark.asyncio
    async def test_execute_task_with_progress_callback(
        self, mock_adapter, mock_model_client
    ):
        """进度回调"""
        progress_calls = []

        async def on_progress(ctx):
            progress_calls.append(ctx.current_step)

        mock_model_client.inference.return_value = {
            "_metadata": "finish",
            "message": "完成"
        }

        executor = TaskExecutor(
            adapter=mock_adapter,
            model_client=mock_model_client,
            on_progress=on_progress
        )

        await executor.execute_task(
            task_id="task_001",
            device_id="device_001",
            instruction="测试",
            model_config={}
        )

        # finish 动作不触发进度回调
        assert len(progress_calls) == 0

    @pytest.mark.asyncio
    async def test_execute_task_with_complete_callback(
        self, mock_adapter, mock_model_client
    ):
        """完成回调"""
        complete_calls = []

        async def on_complete(ctx):
            complete_calls.append(ctx)

        mock_model_client.inference.return_value = {
            "_metadata": "finish",
            "message": "完成"
        }

        executor = TaskExecutor(
            adapter=mock_adapter,
            model_client=mock_model_client,
            on_complete=on_complete
        )

        ctx = await executor.execute_task(
            task_id="task_001",
            device_id="device_001",
            instruction="测试",
            model_config={}
        )

        assert len(complete_calls) == 1
        assert complete_calls[0].status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_task_action_failure(
        self, mock_adapter, mock_model_client
    ):
        """动作执行失败"""
        mock_model_client.inference.return_value = {
            "_metadata": "do",
            "action": "Tap",
            "element": {"x": 500, "y": 300}
        }
        mock_adapter.execute_action.return_value = Mock(
            success=False,
            should_finish=False,
            message="Tap failed: device error"
        )

        executor = TaskExecutor(
            adapter=mock_adapter,
            model_client=mock_model_client
        )

        ctx = await executor.execute_task(
            task_id="task_001",
            device_id="device_001",
            instruction="测试",
            model_config={}
        )

        assert ctx.status == TaskStatus.ERROR
        assert ctx.last_error == "Tap failed: device error"

    @pytest.mark.asyncio
    async def test_interrupt_task(self, mock_adapter, mock_model_client):
        """中断任务"""
        call_count = 0

        async def slow_inference(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return {
                "_metadata": "do",
                "action": "Tap",
                "element": {"x": 500, "y": 300}
            }

        mock_model_client.inference.side_effect = slow_inference

        executor = TaskExecutor(
            adapter=mock_adapter,
            model_client=mock_model_client
        )

        # 启动任务
        task = asyncio.create_task(executor.execute_task(
            task_id="task_001",
            device_id="device_001",
            instruction="测试",
            model_config={},
            max_steps=100
        ))

        # 等待一小段时间后中断
        await asyncio.sleep(0.05)
        executor.interrupt("user_cancelled")

        # 等待任务完成
        try:
            ctx = await asyncio.wait_for(task, timeout=2)
            assert ctx.status == TaskStatus.INTERRUPTED
        except asyncio.TimeoutError:
            pytest.fail("Task did not complete after interrupt")


import asyncio
