"""
网络消息单元测试 - TDD 风格
"""
import pytest
import json
from datetime import datetime
from src.network.messages import (
    MessageType,
    BaseMessage,
    TaskMessage,
    InterruptMessage,
    AckMessage,
    TaskUpdateMessage,
    TaskResultMessage,
    DeviceStatusMessage,
    ErrorMessage,
    HeartbeatMessage,
    WelcomeMessage,
    PongMessage,
    MessageFactory,
)


class TestBaseMessage:
    """基础消息测试"""

    def test_message_has_default_version(self):
        """消息有默认版本"""
        msg = BaseMessage(msg_id="test-001")
        assert msg.version == "1.0"

    def test_message_has_timestamp(self):
        """消息有时间戳"""
        before = datetime.now()
        msg = BaseMessage(msg_id="test-001")
        after = datetime.now()

        assert msg.timestamp.endswith("Z")
        # 简单验证时间戳格式
        assert len(msg.timestamp) > 10

    def test_to_dict(self):
        """转换为字典"""
        msg = BaseMessage(msg_id="test-001")
        data = msg.to_dict()

        assert data["msg_id"] == "test-001"
        assert data["version"] == "1.0"
        assert "timestamp" in data

    def test_to_json(self):
        """转换为 JSON"""
        msg = BaseMessage(msg_id="test-001")
        json_str = msg.to_json()

        data = json.loads(json_str)
        assert data["msg_id"] == "test-001"

    def test_from_dict(self):
        """从字典创建"""
        data = {
            "msg_id": "test-001",
            "version": "1.0",
            "timestamp": "2024-03-15T10:30:00Z"
        }
        msg = BaseMessage.from_dict(data)

        assert msg.msg_id == "test-001"

    def test_from_json(self):
        """从 JSON 创建"""
        json_str = '{"msg_id": "test-001", "version": "1.0"}'
        msg = BaseMessage.from_json(json_str)

        assert msg.msg_id == "test-001"


class TestTaskMessage:
    """任务消息测试"""

    def test_task_message_serialization(self, task_message_data):
        """任务消息序列化"""
        msg = TaskMessage.from_dict(task_message_data)

        data = msg.to_dict()

        assert data["type"] == "task"
        assert data["task_id"] == "task_20240315_001"
        assert data["target"]["device_id"] == "R5CR12345ABC"
        assert data["task"] == "打开微信搜索附近的人"
        assert data["max_steps"] == 100

    def test_task_message_deserialization(self, task_message_data):
        """任务消息反序列化"""
        msg = TaskMessage.from_dict(task_message_data)

        assert msg.task_id == "task_20240315_001"
        assert msg.device_id == "R5CR12345ABC"
        assert msg.platform == "android"
        assert msg.task == "打开微信搜索附近的人"
        assert msg.max_steps == 100
        assert msg.model_config["base_url"] == "http://localhost:8000/v1"

    def test_task_message_device_id_property(self, task_message_data):
        """device_id 属性正确获取"""
        msg = TaskMessage.from_dict(task_message_data)

        assert msg.device_id == "R5CR12345ABC"

    def test_task_message_platform_property(self, task_message_data):
        """platform 属性正确获取"""
        msg = TaskMessage.from_dict(task_message_data)

        assert msg.platform == "android"

    def test_task_message_defaults(self):
        """任务消息默认值"""
        msg = TaskMessage(
            msg_id="test-001",
            task_id="task-001",
            task="测试任务"
        )

        assert msg.max_steps == 100
        assert msg.priority == 1
        assert msg.screenshot_config["upload_on"] == ["error", "finish", "interrupted", "interval:5"]


class TestInterruptMessage:
    """中断消息测试"""

    def test_interrupt_message_serialization(self, interrupt_message_data):
        """中断消息序列化"""
        msg = InterruptMessage.from_dict(interrupt_message_data)

        data = msg.to_dict()

        assert data["type"] == "interrupt"
        assert data["task_id"] == "task_20240315_001"
        assert data["reason"] == "user_cancelled"

    def test_interrupt_message_deserialization(self, interrupt_message_data):
        """中断消息反序列化"""
        msg = InterruptMessage.from_dict(interrupt_message_data)

        assert msg.task_id == "task_20240315_001"
        assert msg.reason == "user_cancelled"

    def test_interrupt_reason_options(self):
        """中断原因选项"""
        reasons = ["user_cancelled", "system_emergency"]

        for reason in reasons:
            msg = InterruptMessage(
                msg_id="test-001",
                task_id="task-001",
                reason=reason
            )
            assert msg.reason == reason


class TestAckMessage:
    """ACK 消息测试"""

    def test_ack_message_serialization(self):
        """ACK 消息序列化"""
        msg = AckMessage(
            msg_id="ack-001",
            ref_msg_id="msg-001",
            accepted=True,
            device_status="busy"
        )

        data = msg.to_dict()

        assert data["type"] == "ack"
        assert data["ref_msg_id"] == "msg-001"
        assert data["accepted"] is True
        assert data["device_status"] == "busy"

    def test_ack_with_error(self):
        """带错误的 ACK"""
        msg = AckMessage(
            msg_id="ack-001",
            ref_msg_id="msg-001",
            accepted=False,
            device_status="idle",
            error="device not found"
        )

        data = msg.to_dict()

        assert data["accepted"] is False
        assert data["error"] == "device not found"


class TestTaskUpdateMessage:
    """任务进度更新消息测试"""

    def test_create_progress_update(self):
        """创建进度更新"""
        msg = TaskUpdateMessage.create(
            task_id="task-001",
            device_id="device-001",
            current_step=5,
            max_steps=100,
            current_action="tap"
        )

        assert msg.task_id == "task-001"
        assert msg.status == "running"
        assert msg.progress["current_step"] == 5
        assert msg.progress["current_action"] == "tap"

    def test_progress_update_with_screenshot(self):
        """带截图的进度更新"""
        msg = TaskUpdateMessage.create(
            task_id="task-001",
            device_id="device-001",
            current_step=5,
            max_steps=100,
            current_action="tap",
            screenshot_path="/screenshots/step_5.png"
        )

        assert msg.progress["screenshot_url"] == "/screenshots/step_5.png"


class TestTaskResultMessage:
    """任务结果消息测试"""

    def test_create_success_result(self):
        """创建成功结果"""
        msg = TaskResultMessage.create_success(
            task_id="task-001",
            device_id="device-001",
            finish_message="任务完成",
            total_steps=10,
            duration_seconds=30.5,
            screenshots=["/screenshots/step_0.png", "/screenshots/step_1.png"]
        )

        assert msg.status == "completed"
        assert msg.result["finish_message"] == "任务完成"
        assert msg.result["total_steps"] == 10

    def test_create_error_result(self):
        """创建错误结果"""
        msg = TaskResultMessage.create_error(
            task_id="task-001",
            device_id="device-001",
            error_message="截图失败",
            failed_step=5
        )

        assert msg.status == "error"
        assert msg.result["error_message"] == "截图失败"
        assert msg.result["failed_step"] == 5


class TestDeviceStatusMessage:
    """设备状态消息测试"""

    def test_create_device_status(self):
        """创建设备状态消息"""
        devices = [
            {
                "device_id": "device-001",
                "platform": "android",
                "status": "idle",
                "model": "MI 13 Pro",
                "connection": "usb"
            },
            {
                "device_id": "device-002",
                "platform": "harmonyos",
                "status": "busy",
                "model": "HUAWEI P50"
            }
        ]

        msg = DeviceStatusMessage.create(
            client_id="client-001",
            devices=devices
        )

        assert msg.client_id == "client-001"
        assert len(msg.devices) == 2
        assert msg.devices[0]["device_id"] == "device-001"


class TestErrorMessage:
    """错误消息测试"""

    def test_create_error_message(self):
        """创建错误消息"""
        msg = ErrorMessage.create(
            code="STEP_TIMEOUT",
            message="单步执行超时",
            task_id="task-001",
            device_id="device-001",
            step=5
        )

        assert msg.error["code"] == "STEP_TIMEOUT"
        assert msg.error["message"] == "单步执行超时"
        assert msg.error["step"] == 5


class TestHeartbeatMessage:
    """心跳消息测试"""

    def test_heartbeat_serialization(self):
        """心跳消息序列化"""
        msg = HeartbeatMessage(
            msg_id="hb-001",
            client_id="client-001",
            client_status="online"
        )

        data = msg.to_dict()

        assert data["type"] == "heartbeat"
        assert data["client_id"] == "client-001"
        assert data["client_status"] == "online"


class TestWelcomeMessage:
    """欢迎消息测试"""

    def test_welcome_serialization(self):
        """欢迎消息序列化"""
        msg = WelcomeMessage(
            msg_id="welcome-001",
            session_id="session-123"
        )

        data = msg.to_dict()

        assert data["type"] == "welcome"
        assert data["session_id"] == "session-123"


class TestMessageFactory:
    """消息工厂测试"""

    def test_factory_task_message(self, task_message_data):
        """工厂创建任务消息"""
        msg = MessageFactory.from_dict(task_message_data)

        assert isinstance(msg, TaskMessage)
        assert msg.task_id == "task_20240315_001"

    def test_factory_interrupt_message(self, interrupt_message_data):
        """工厂创建中断消息"""
        msg = MessageFactory.from_dict(interrupt_message_data)

        assert isinstance(msg, InterruptMessage)
        assert msg.task_id == "task_20240315_001"

    def test_factory_heartbeat_message(self):
        """工厂创建心跳消息"""
        data = {
            "type": "heartbeat",
            "msg_id": "hb-001",
            "client_id": "client-001",
            "client_status": "online"
        }

        msg = MessageFactory.from_dict(data)

        assert isinstance(msg, HeartbeatMessage)

    def test_factory_unknown_type_raises_error(self):
        """未知类型抛出异常"""
        data = {
            "type": "unknown_type",
            "msg_id": "test-001"
        }

        with pytest.raises(ValueError, match="Unknown message type"):
            MessageFactory.from_dict(data)

    def test_factory_from_json(self, task_message_data):
        """从 JSON 创建消息"""
        json_str = json.dumps(task_message_data)
        msg = MessageFactory.from_json(json_str)

        assert isinstance(msg, TaskMessage)


class TestMessageRoundTrip:
    """消息往返测试"""

    def test_task_message_roundtrip(self, task_message_data):
        """任务消息往返序列化"""
        # 创建
        msg1 = TaskMessage.from_dict(task_message_data)

        # 序列化
        json_str = msg1.to_json()

        # 反序列化
        msg2 = MessageFactory.from_json(json_str)

        # 验证
        assert isinstance(msg2, TaskMessage)
        assert msg2.task_id == msg1.task_id
        assert msg2.device_id == msg1.device_id
        assert msg2.task == msg1.task

    def test_interrupt_message_roundtrip(self, interrupt_message_data):
        """中断消息往返序列化"""
        msg1 = InterruptMessage.from_dict(interrupt_message_data)

        json_str = msg1.to_json()
        msg2 = MessageFactory.from_json(json_str)

        assert isinstance(msg2, InterruptMessage)
        assert msg2.task_id == msg1.task_id
        assert msg2.reason == msg1.reason
