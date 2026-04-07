"""
日志模块测试
"""
import json
import tempfile
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from src.logging.logger import (
    ClientLogger,
    LogLevel,
    LogEvent,
    LogConfig,
    LogEntry,
)


class TestLogConfig:
    """日志配置测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = LogConfig()
        assert config.local_path == "./logs"
        assert config.retention_days == 30
        assert config.level == "INFO"

    def test_get_level(self):
        """测试获取日志级别"""
        config = LogConfig(level="DEBUG")
        assert config.get_level() == 10  # logging.DEBUG


class TestLogEntry:
    """日志条目测试"""

    def test_create_entry(self):
        """测试创建日志条目"""
        entry = LogEntry(
            log_id="test-123",
            timestamp="2024-01-01T10:00:00Z",
            level="INFO",
            source={"client_id": "client-001"},
            event="test_event",
            data={"key": "value"},
            tags=["tag1"],
        )

        assert entry.log_id == "test-123"
        assert entry.level == "INFO"
        assert entry.event == "test_event"

    def test_to_dict(self):
        """测试转换为字典"""
        entry = LogEntry(
            log_id="test-123",
            timestamp="2024-01-01T10:00:00Z",
            level="INFO",
            source={"client_id": "client-001"},
            event="test_event",
        )

        data = entry.to_dict()
        assert isinstance(data, dict)
        assert data["log_id"] == "test-123"

    def test_to_json(self):
        """测试转换为 JSON"""
        entry = LogEntry(
            log_id="test-123",
            timestamp="2024-01-01T10:00:00Z",
            level="INFO",
            source={"client_id": "client-001"},
            event="test_event",
        )

        json_str = entry.to_json()
        assert isinstance(json_str, str)
        assert "test-123" in json_str

    def test_from_dict(self):
        """测试从字典创建"""
        data = {
            "log_id": "test-123",
            "timestamp": "2024-01-01T10:00:00Z",
            "level": "INFO",
            "source": {"client_id": "client-001"},
            "event": "test_event",
            "data": {},
            "tags": [],
        }

        entry = LogEntry.from_dict(data)
        assert entry.log_id == "test-123"
        assert entry.event == "test_event"


class TestClientLogger:
    """客户端日志测试"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def config(self, temp_dir):
        """创建测试配置"""
        return LogConfig(
            local_path=temp_dir,
            retention_days=30,
            level="DEBUG",
        )

    @pytest.fixture
    def logger(self, config):
        """创建日志记录器"""
        return ClientLogger(
            config=config,
            client_id="test-client",
        )

    def test_log_basic(self, logger, temp_dir):
        """测试基本日志记录"""
        entry = logger.log(
            event="test_event",
            level="INFO",
            message="Test message",
        )

        assert entry.event == "test_event"
        assert entry.level == "INFO"
        assert entry.source["client_id"] == "test-client"

        # 验证文件写入
        log_file = Path(temp_dir) / f"client_test-client_{datetime.now().strftime('%Y%m%d')}.jsonl"
        assert log_file.exists()

    def test_log_with_context(self, logger, temp_dir):
        """测试带上下文的日志"""
        entry = logger.log(
            event="task_started",
            task_id="task-001",
            device_id="device-001",
            data={"step": 1},
        )

        assert entry.source["task_id"] == "task-001"
        assert entry.source["device_id"] == "device-001"
        assert entry.data["step"] == 1

    def test_info_debug_warning_error(self, logger):
        """测试便捷方法"""
        entry1 = logger.info("info_event")
        entry2 = logger.debug("debug_event")
        entry3 = logger.warning("warning_event")
        entry4 = logger.error("error_event")

        assert entry1.level == "INFO"
        assert entry2.level == "DEBUG"
        assert entry3.level == "WARNING"
        assert entry4.level == "ERROR"

    def test_log_client_events(self, logger):
        """测试客户端事件记录"""
        entry1 = logger.log_client_started()
        assert entry1.event == LogEvent.CLIENT_STARTED.value

        entry2 = logger.log_client_connected("session-123")
        assert entry2.event == LogEvent.CLIENT_CONNECTED.value
        assert entry2.data["session_id"] == "session-123"

        entry3 = logger.log_client_disconnected("network error")
        assert entry3.event == LogEvent.CLIENT_DISCONNECTED.value

    def test_log_device_events(self, logger):
        """测试设备事件记录"""
        entry1 = logger.log_device_connected("device-001", "android")
        assert entry1.event == LogEvent.DEVICE_CONNECTED.value
        assert entry1.source["device_id"] == "device-001"

        entry2 = logger.log_device_disconnected("device-001")
        assert entry2.event == LogEvent.DEVICE_DISCONNECTED.value

    def test_log_task_events(self, logger):
        """测试任务事件记录"""
        entry1 = logger.log_task_received("task-001", "device-001", "打开微信")
        assert entry1.event == LogEvent.TASK_RECEIVED.value
        assert entry1.data["instruction"] == "打开微信"

        entry2 = logger.log_task_started("task-001", "device-001")
        assert entry2.event == LogEvent.TASK_STARTED.value

        entry3 = logger.log_action_executed("task-001", "device-001", "tap", True, 150)
        assert entry3.event == LogEvent.ACTION_EXECUTED.value
        assert entry3.data["action"] == "tap"
        assert entry3.data["duration_ms"] == 150

        entry4 = logger.log_action_failed("task-001", "device-001", "tap", "Element not found")
        assert entry4.event == LogEvent.ACTION_FAILED.value

        entry5 = logger.log_task_completed("task-001", "device-001", 10, 45.5)
        assert entry5.event == LogEvent.TASK_COMPLETED.value
        assert entry5.data["total_steps"] == 10

        entry6 = logger.log_task_failed("task-001", "device-001", "Max steps reached")
        assert entry6.event == LogEvent.TASK_FAILED.value

    def test_query_logs(self, logger, temp_dir):
        """测试日志查询"""
        # 创建一些日志
        logger.log("event_1", level="INFO")
        logger.log("event_2", level="DEBUG")
        logger.log("event_3", level="ERROR")

        # 查询所有
        results = logger.query()
        assert len(results) >= 3

        # 按级别查询
        results = logger.query(level="ERROR")
        assert all(r.level == "ERROR" for r in results)

        # 按事件查询
        results = logger.query(event="event_1")
        assert all(r.event == "event_1" for r in results)

    def test_query_with_time_range(self, logger):
        """测试按时间范围查询"""
        logger.log("event_now", level="INFO")

        now = datetime.now()
        yesterday = now - timedelta(days=1)

        results = logger.query(start_time=yesterday, end_time=now)
        assert len(results) >= 1

    def test_query_with_pagination(self, logger):
        """测试分页查询"""
        # 创建多条日志
        for i in range(10):
            logger.log(f"event_{i}", level="INFO")

        # 测试分页
        page1 = logger.query(limit=3, offset=0)
        page2 = logger.query(limit=3, offset=3)

        assert len(page1) == 3
        assert len(page2) == 3

    def test_cleanup_old_logs(self, logger, temp_dir):
        """测试清理旧日志"""
        # 创建一个旧日志文件
        old_date = (datetime.now() - timedelta(days=35)).strftime("%Y%m%d")
        old_file = Path(temp_dir) / f"client_test-client_{old_date}.jsonl"
        old_file.write_text('{"log_id": "old", "timestamp": "2024-01-01", "level": "INFO", "source": {}, "event": "old", "data": {}, "tags": []}\n')

        # 创建一个新日志文件
        logger.log("recent_event", level="INFO")

        # 清理
        cleaned = logger.cleanup_old_logs(days=30)

        # 验证
        assert cleaned >= 1
        assert not old_file.exists()

    def test_get_log_stats(self, logger, temp_dir):
        """测试获取日志统计"""
        logger.log("event_1", level="INFO")
        logger.log("event_2", level="INFO")

        stats = logger.get_log_stats()

        assert stats["file_count"] >= 1
        assert stats["total_size_mb"] >= 0
        assert stats["retention_days"] == 30

    def test_on_log_callback(self, config):
        """测试日志回调"""
        received = []

        def on_log(entry):
            received.append(entry)

        logger = ClientLogger(config=config, client_id="test", on_log=on_log)
        logger.log("test_event", level="INFO")

        assert len(received) == 1
        assert received[0].event == "test_event"

    def test_close(self, logger):
        """测试关闭日志系统"""
        logger.log("event_before_close", level="INFO")
        logger.close()
        logger.log("event_after_close", level="INFO")  # 应该仍然工作
