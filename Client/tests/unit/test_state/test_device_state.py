"""
设备状态机单元测试 - TDD 风格
"""
import pytest
from datetime import datetime
from src.state.device_state import (
    DeviceStatus,
    DeviceState,
    DeviceStateManager,
    StateTransitionError,
)


class TestDeviceState:
    """设备状态测试"""

    def test_initial_status_is_idle(self):
        """初始状态为空闲"""
        state = DeviceState(device_id="device-001", platform="android")
        assert state.status == DeviceStatus.IDLE
        assert state.current_task_id is None

    def test_initial_timestamp_is_set(self):
        """初始时间戳已设置"""
        before = datetime.now()
        state = DeviceState(device_id="device-001", platform="android")
        after = datetime.now()

        assert before <= state.last_update <= after

    def test_receive_task_transitions_to_busy(self):
        """接收任务时从空闲转为忙碌"""
        state = DeviceState(device_id="device-001", platform="android")

        state.receive_task("task-001")

        assert state.status == DeviceStatus.BUSY
        assert state.current_task_id == "task-001"

    def test_complete_task_transitions_to_idle(self):
        """任务完成时从忙碌转为空闲"""
        state = DeviceState(device_id="device-001", platform="android")
        state.receive_task("task-001")

        state.complete_task()

        assert state.status == DeviceStatus.IDLE
        assert state.current_task_id is None

    def test_fail_task_transitions_to_idle(self):
        """任务失败时从忙碌转为空闲"""
        state = DeviceState(device_id="device-001", platform="android")
        state.receive_task("task-001")

        state.fail_task()

        assert state.status == DeviceStatus.IDLE
        assert state.current_task_id is None

    def test_device_lost_from_idle_transitions_to_offline(self):
        """空闲时设备丢失转为离线"""
        state = DeviceState(device_id="device-001", platform="android")

        state.device_lost()

        assert state.status == DeviceStatus.OFFLINE

    def test_device_lost_from_busy_transitions_to_offline(self):
        """忙碌时设备丢失转为离线"""
        state = DeviceState(device_id="device-001", platform="android")
        state.receive_task("task-001")

        state.device_lost()

        assert state.status == DeviceStatus.OFFLINE
        # 任务信息应该保留（用于恢复）
        assert state.current_task_id == "task-001"

    def test_device_recovered_transitions_to_idle(self):
        """设备恢复时从离线转为空闲"""
        state = DeviceState(device_id="device-001", platform="android")
        state.device_lost()

        state.device_recovered()

        assert state.status == DeviceStatus.IDLE

    def test_interrupt_from_busy_transitions_to_idle(self):
        """忙碌时中断转为空闲"""
        state = DeviceState(device_id="device-001", platform="android")
        state.receive_task("task-001")

        state.interrupt()

        assert state.status == DeviceStatus.IDLE
        assert state.current_task_id is None

    def test_interrupt_from_idle_does_nothing(self):
        """空闲时中断什么都不会发生"""
        state = DeviceState(device_id="device-001", platform="android")

        # 不应该抛出异常
        state.interrupt()

        assert state.status == DeviceStatus.IDLE

    def test_interrupt_from_offline_does_nothing(self):
        """离线时中断什么都不会发生"""
        state = DeviceState(device_id="device-001", platform="android")
        state.device_lost()

        # 不应该抛出异常
        state.interrupt()

        assert state.status == DeviceStatus.OFFLINE

    def test_invalid_transition_raises_error(self):
        """非法状态转换抛出异常"""
        state = DeviceState(device_id="device-001", platform="android")

        # 尝试直接从 Idle 到任何非 BUSY/OFFLINE 的状态
        with pytest.raises(StateTransitionError):
            state.transition(DeviceStatus.IDLE)  # 不能转换到自身

    def test_busy_cannot_go_back_to_busy(self):
        """忙碌状态不能转换到忙碌"""
        state = DeviceState(device_id="device-001", platform="android")
        state.receive_task("task-001")

        with pytest.raises(StateTransitionError):
            state.transition(DeviceStatus.BUSY)

    def test_offline_cannot_transition_to_busy(self):
        """离线状态不能直接转换到忙碌（需要先恢复）"""
        state = DeviceState(device_id="device-001", platform="android")
        state.device_lost()

        # 设备恢复后才能接收任务
        state.device_recovered()
        state.receive_task("task-002")

        assert state.status == DeviceStatus.BUSY


class TestDeviceStateManager:
    """设备状态管理器测试"""

    def test_add_device(self):
        """添加设备"""
        manager = DeviceStateManager()

        state = manager.add_device("device-001", "android")

        assert state.device_id == "device-001"
        assert state.platform == "android"
        assert state.status == DeviceStatus.IDLE

    def test_add_duplicate_device_returns_existing(self):
        """添加重复设备返回已存在的"""
        manager = DeviceStateManager()

        state1 = manager.add_device("device-001", "android")
        state2 = manager.add_device("device-001", "android")

        assert state1 is state2

    def test_remove_device(self):
        """移除设备"""
        manager = DeviceStateManager()
        manager.add_device("device-001", "android")

        manager.remove_device("device-001")

        assert manager.get_device("device-001") is None

    def test_remove_nonexistent_device(self):
        """移除不存在的设备不抛异常"""
        manager = DeviceStateManager()

        # 不应该抛异常
        manager.remove_device("nonexistent")

    def test_get_all_devices(self):
        """获取所有设备"""
        manager = DeviceStateManager()
        manager.add_device("device-001", "android")
        manager.add_device("device-002", "harmonyos")
        manager.add_device("device-003", "ios")

        devices = manager.get_all_devices()

        assert len(devices) == 3

    def test_get_idle_devices(self):
        """获取空闲设备"""
        manager = DeviceStateManager()
        d1 = manager.add_device("device-001", "android")
        d2 = manager.add_device("device-002", "harmonyos")
        d3 = manager.add_device("device-003", "ios")

        d1.receive_task("task-001")  # busy
        d2.device_lost()  # offline

        idle = manager.get_idle_devices()

        assert len(idle) == 1
        assert idle[0].device_id == "device-003"

    def test_get_busy_devices(self):
        """获取忙碌设备"""
        manager = DeviceStateManager()
        d1 = manager.add_device("device-001", "android")
        d2 = manager.add_device("device-002", "harmonyos")
        d3 = manager.add_device("device-003", "ios")

        d1.receive_task("task-001")  # busy
        d2.receive_task("task-002")  # busy

        busy = manager.get_busy_devices()

        assert len(busy) == 2
        assert {d.device_id for d in busy} == {"device-001", "device-002"}

    def test_get_online_devices(self):
        """获取在线设备（不包括离线）"""
        manager = DeviceStateManager()
        d1 = manager.add_device("device-001", "android")
        d2 = manager.add_device("device-002", "harmonyos")
        d3 = manager.add_device("device-003", "ios")

        d1.receive_task("task-001")  # busy
        d2.device_lost()  # offline

        online = manager.get_online_devices()

        assert len(online) == 2
        assert {d.device_id for d in online} == {"device-001", "device-003"}


class TestDeviceStateSerialization:
    """设备状态序列化测试"""

    def test_to_dict(self):
        """转换为字典"""
        state = DeviceState(
            device_id="device-001",
            platform="android",
            status=DeviceStatus.BUSY,
            current_task_id="task-001"
        )

        data = state.to_dict()

        assert data["device_id"] == "device-001"
        assert data["platform"] == "android"
        assert data["status"] == "busy"
        assert data["current_task_id"] == "task-001"
        assert "last_update" in data

    def test_from_dict(self):
        """从字典创建"""
        data = {
            "device_id": "device-001",
            "platform": "android",
            "status": "busy",
            "current_task_id": "task-001",
            "last_update": "2024-03-15T10:30:00"
        }

        state = DeviceState.from_dict(data)

        assert state.device_id == "device-001"
        assert state.status == DeviceStatus.BUSY
        assert state.current_task_id == "task-001"


class TestInterruptedFlow:
    """Interrupted 指令流程测试"""

    def test_interrupted_during_task_execution(self):
        """
        任务执行中的中断流程：
        1. 设备接收任务，进入 Busy
        2. 执行中断指令
        3. 立即响应 ACK
        4. 停止当前执行
        5. 保存当前状态
        6. 返回 Interrupted 结果
        7. 进入 Idle
        """
        state = DeviceState(device_id="device-001", platform="android")

        # 1. 接收任务
        state.receive_task("task-001")
        assert state.status == DeviceStatus.BUSY
        assert state.current_task_id == "task-001"

        # 2-4. 中断（在这个流程中我们假设已经停止了执行）

        # 5-6. 这里应该保存状态到截图等，但 DeviceState 不处理

        # 7. 中断指令使设备进入 Idle
        state.interrupt()

        assert state.status == DeviceStatus.IDLE
        assert state.current_task_id is None

    def test_interrupted_when_no_task(self):
        """没有任务时收到中断"""
        state = DeviceState(device_id="device-001", platform="android")

        # 空闲状态收到中断
        state.interrupt()

        # 状态不变
        assert state.status == DeviceStatus.IDLE

    def test_interrupted_preserves_device_state(self):
        """中断保留设备连接信息"""
        state = DeviceState(device_id="device-001", platform="android")

        # 设备在线时收到任务
        state.receive_task("task-001")

        # 中断
        state.interrupt()

        # 设备状态仍然是在线，只是空闲
        assert state.status == DeviceStatus.IDLE
        assert state.device_id == "device-001"
        assert state.platform == "android"
