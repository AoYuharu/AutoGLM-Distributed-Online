"""
客户端日志系统

提供结构化日志记录，支持本地存储和日志拉取
参照 DESIGN.md 中的日志审计设计
"""
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Callable
from threading import Lock


class LogLevel(Enum):
    """日志级别"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# 日志模块分组
LOG_MODULES = {
    "adapter": "适配器操作",
    "executor": "任务执行",
    "state": "状态机变化",
    "network": "网络通信",
    "polling": "设备轮询",
    "screenshot": "截图管理",
}


class LogEvent(Enum):
    """日志事件类型"""
    # 客户端事件
    CLIENT_STARTED = "client_started"
    CLIENT_CONNECTED = "client_connected"
    CLIENT_DISCONNECTED = "client_disconnected"

    # 设备事件
    DEVICE_CONNECTED = "device_connected"
    DEVICE_DISCONNECTED = "device_disconnected"
    DEVICE_STATUS_CHANGED = "device_status_changed"

    # 任务事件
    TASK_RECEIVED = "task_received"
    TASK_STARTED = "task_started"
    ACTION_EXECUTED = "action_executed"
    ACTION_FAILED = "action_failed"
    MODEL_REQUEST = "model_request"
    MODEL_RESPONSE = "model_response"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    INTERRUPT_RECEIVED = "interrupt_received"

    # 系统事件
    ERROR = "error"


@dataclass
class LogConfig:
    """日志配置"""
    local_path: str = "./logs"
    retention_days: int = 30
    max_size_mb: int = 1024
    level: str = "INFO"
    rotation: str = "daily"  # daily | size
    max_files: int = 100

    def get_level(self) -> int:
        """获取日志级别数值"""
        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        return levels.get(self.level, logging.INFO)


@dataclass
class LogEntry:
    """日志条目"""
    log_id: str
    timestamp: str
    level: str
    source: dict
    event: str
    data: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    def to_json(self) -> str:
        """转换为 JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "LogEntry":
        """从字典创建"""
        return cls(**data)


class ClientLogger:
    """
    客户端日志系统

    负责：
    - 结构化日志记录
    - 本地文件存储
    - 日志过滤和查询
    - 日志轮转和清理
    """

    def __init__(
        self,
        config: Optional[LogConfig] = None,
        client_id: str = "unknown",
        on_log: Optional[Callable[[LogEntry], None]] = None,
    ):
        """
        初始化日志系统

        Args:
            config: 日志配置
            client_id: 客户端 ID
            on_log: 日志回调（实时推送）
        """
        self.config = config or LogConfig()
        self.client_id = client_id
        self.on_log = on_log

        # 确保日志目录存在
        Path(self.config.local_path).mkdir(parents=True, exist_ok=True)

        # 内部状态
        self._lock = Lock()
        self._log_buffer: list[LogEntry] = []
        self._current_file: Optional[Path] = None
        self._file_date: Optional[str] = None

        # 设置标准日志
        self._setup_standard_logger()

    def _setup_standard_logger(self) -> None:
        """设置标准日志记录器"""
        self._logger = logging.getLogger(f"client.{self.client_id}")
        self._logger.setLevel(self.config.get_level())

        # 避免重复添加 handler
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setLevel(self.config.get_level())
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

        # 模块日志器缓存
        self._module_loggers: dict[str, logging.Logger] = {}

    def get_module_logger(self, module: str) -> logging.Logger:
        """
        获取指定模块的日志记录器，输出到独立文件

        Args:
            module: 模块名称，如 'adapter', 'executor', 'state', 'network', 'polling', 'screenshot'

        Returns:
            模块专属的 Logger
        """
        if module in self._module_loggers:
            return self._module_loggers[module]

        logger_name = f"client.{self.client_id}.{module}"
        module_logger = logging.getLogger(logger_name)
        module_logger.setLevel(self.config.get_level())

        # 创建模块专属的日志文件
        if module_logger.handlers:
            # 已有 handler，直接返回
            self._module_loggers[module] = module_logger
            return module_logger

        today = datetime.now().strftime("%Y%m%d")
        filename = f"client_{self.client_id}_{module}_{today}.log"
        filepath = Path(self.config.local_path) / filename

        # 文件 handler
        file_handler = logging.FileHandler(filepath, encoding="utf-8")
        file_handler.setLevel(self.config.get_level())
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)

        # 控制台 handler（可选，用于调试）
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)  # 模块日志只在文件中
        console_handler.setFormatter(file_formatter)

        module_logger.addHandler(file_handler)
        module_logger.addHandler(console_handler)

        self._module_loggers[module] = module_logger
        return module_logger

    def _get_log_file(self) -> Path:
        """获取当前日志文件路径"""
        today = datetime.now().strftime("%Y%m%d")

        if self._current_file is None or self._file_date != today:
            filename = f"client_{self.client_id}_{today}.jsonl"
            self._current_file = Path(self.config.local_path) / filename
            self._file_date = today

        return self._current_file

    def log(
        self,
        event: str,
        level: str = "INFO",
        task_id: Optional[str] = None,
        device_id: Optional[str] = None,
        data: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        message: Optional[str] = None,
    ) -> LogEntry:
        """
        记录日志

        Args:
            event: 事件类型
            level: 日志级别
            task_id: 任务 ID
            device_id: 设备 ID
            data: 事件数据
            tags: 标签
            message: 日志消息

        Returns:
            日志条目
        """
        source = {
            "client_id": self.client_id,
        }
        if device_id:
            source["device_id"] = device_id
        if task_id:
            source["task_id"] = task_id

        entry = LogEntry(
            log_id=str(uuid.uuid4()),
            timestamp=datetime.now().isoformat() + "Z",
            level=level,
            source=source,
            event=event,
            data=data or {},
            tags=tags or [],
        )

        # 添加消息到 data
        if message:
            entry.data["message"] = message

        # 记录到标准日志
        log_message = f"[{event}] {message or ''}"
        if level == "DEBUG":
            self._logger.debug(log_message)
        elif level == "INFO":
            self._logger.info(log_message)
        elif level == "WARNING":
            self._logger.warning(log_message)
        elif level == "ERROR":
            self._logger.error(log_message)

        # 持久化
        self._write_to_file(entry)

        # 实时推送
        if self.on_log:
            try:
                self.on_log(entry)
            except Exception:
                pass

        return entry

    def _write_to_file(self, entry: LogEntry) -> None:
        """写入日志文件"""
        with self._lock:
            try:
                filepath = self._get_log_file()
                with open(filepath, "a", encoding="utf-8") as f:
                    f.write(entry.to_json() + "\n")
            except Exception as e:
                self._logger.error(f"Failed to write log: {e}")

    # === 便捷方法 ===

    def info(self, event: str, **kwargs) -> LogEntry:
        """记录 INFO 日志"""
        return self.log(event, level="INFO", **kwargs)

    def debug(self, event: str, **kwargs) -> LogEntry:
        """记录 DEBUG 日志"""
        return self.log(event, level="DEBUG", **kwargs)

    def warning(self, event: str, **kwargs) -> LogEntry:
        """记录 WARNING 日志"""
        return self.log(event, level="WARNING", **kwargs)

    def error(self, event: str, **kwargs) -> LogEntry:
        """记录 ERROR 日志"""
        return self.log(event, level="ERROR", **kwargs)

    # === 特定事件方法 ===

    def log_client_started(self) -> LogEntry:
        """记录客户端启动"""
        return self.info(LogEvent.CLIENT_STARTED.value)

    def log_client_connected(self, session_id: str) -> LogEntry:
        """记录客户端连接"""
        return self.info(
            LogEvent.CLIENT_CONNECTED.value,
            data={"session_id": session_id},
        )

    def log_client_disconnected(self, reason: Optional[str] = None) -> LogEntry:
        """记录客户端断开"""
        return self.info(
            LogEvent.CLIENT_DISCONNECTED.value,
            data={"reason": reason} if reason else {},
        )

    def log_device_connected(self, device_id: str, platform: str) -> LogEntry:
        """记录设备连接"""
        return self.info(
            LogEvent.DEVICE_CONNECTED.value,
            device_id=device_id,
            data={"platform": platform},
            tags=["device"],
        )

    def log_device_disconnected(self, device_id: str) -> LogEntry:
        """记录设备断开"""
        return self.info(
            LogEvent.DEVICE_DISCONNECTED.value,
            device_id=device_id,
            tags=["device"],
        )

    def log_task_received(self, task_id: str, device_id: str, instruction: str) -> LogEntry:
        """记录收到任务"""
        return self.info(
            LogEvent.TASK_RECEIVED.value,
            task_id=task_id,
            device_id=device_id,
            data={"instruction": instruction},
            tags=["task"],
        )

    def log_task_started(self, task_id: str, device_id: str) -> LogEntry:
        """记录任务开始"""
        return self.info(
            LogEvent.TASK_STARTED.value,
            task_id=task_id,
            device_id=device_id,
            tags=["task"],
        )

    def log_action_executed(
        self,
        task_id: str,
        device_id: str,
        action: str,
        success: bool,
        duration_ms: int,
    ) -> LogEntry:
        """记录动作执行"""
        return self.info(
            LogEvent.ACTION_EXECUTED.value,
            task_id=task_id,
            device_id=device_id,
            data={
                "action": action,
                "success": success,
                "duration_ms": duration_ms,
            },
            tags=["task", "action"],
        )

    def log_action_failed(
        self,
        task_id: str,
        device_id: str,
        action: str,
        error: str,
    ) -> LogEntry:
        """记录动作失败"""
        return self.error(
            LogEvent.ACTION_FAILED.value,
            task_id=task_id,
            device_id=device_id,
            data={
                "action": action,
                "error": error,
            },
            tags=["task", "action", "error"],
        )

    def log_task_completed(
        self,
        task_id: str,
        device_id: str,
        total_steps: int,
        duration_seconds: float,
    ) -> LogEntry:
        """记录任务完成"""
        return self.info(
            LogEvent.TASK_COMPLETED.value,
            task_id=task_id,
            device_id=device_id,
            data={
                "total_steps": total_steps,
                "duration_seconds": duration_seconds,
            },
            tags=["task"],
        )

    def log_task_failed(
        self,
        task_id: str,
        device_id: str,
        error: str,
    ) -> LogEntry:
        """记录任务失败"""
        return self.error(
            LogEvent.TASK_FAILED.value,
            task_id=task_id,
            device_id=device_id,
            data={"error": error},
            tags=["task", "error"],
        )

    def log_interrupt_received(
        self,
        task_id: str,
        device_id: Optional[str],
        reason: str,
    ) -> LogEntry:
        """记录中断指令"""
        return self.info(
            LogEvent.INTERRUPT_RECEIVED.value,
            task_id=task_id,
            device_id=device_id,
            data={"reason": reason},
            tags=["interrupt"],
        )

    def log_network_outgoing(self, msg_type: str, payload: dict) -> LogEntry:
        """记录发出的网络消息（用于归档）"""
        return self.info(
            "network_outgoing",
            data={
                "msg_type": msg_type,
                "payload": payload,
            },
            tags=["network"],
        )

    def log_network_incoming(self, msg_type: str, payload: dict) -> LogEntry:
        """记录收到的网络消息（用于归档）"""
        return self.info(
            "network_incoming",
            data={
                "msg_type": msg_type,
                "payload": payload,
            },
            tags=["network"],
        )

    # === 日志查询 ===

    def query(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        level: Optional[str] = None,
        event: Optional[str] = None,
        task_id: Optional[str] = None,
        device_id: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[LogEntry]:
        """
        查询日志

        Args:
            start_time: 开始时间
            end_time: 结束时间
            level: 日志级别
            event: 事件类型
            task_id: 任务 ID
            device_id: 设备 ID
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            日志条目列表
        """
        results: list[LogEntry] = []
        logs_dir = Path(self.config.local_path)

        if not logs_dir.exists():
            return results

        # 确定要读取的文件
        if start_time and end_time:
            files = self._get_files_in_range(logs_dir, start_time, end_time)
        else:
            # 默认读取最近的文件
            files = sorted(logs_dir.glob("client_*.jsonl"), key=lambda p: p.name)[-10:]

        for filepath in files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue

                        try:
                            data = json.loads(line)
                            entry = LogEntry.from_dict(data)

                            # 应用过滤器
                            if not self._matches_filter(entry, start_time, end_time, level, event, task_id, device_id):
                                continue

                            results.append(entry)

                        except json.JSONDecodeError:
                            continue

            except Exception as e:
                self._logger.warning(f"Failed to read log file {filepath}: {e}")

        # 排序（按时间倒序）
        results.sort(key=lambda x: x.timestamp, reverse=True)

        # 分页
        return results[offset:offset + limit]

    def _matches_filter(
        self,
        entry: LogEntry,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        level: Optional[str],
        event: Optional[str],
        task_id: Optional[str],
        device_id: Optional[str],
    ) -> bool:
        """检查日志条目是否匹配过滤器"""
        # 时间过滤
        if start_time or end_time:
            entry_time = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
            # 统一为 aware datetime 进行比较
            if start_time:
                start_aware = start_time if start_time.tzinfo else start_time.replace(tzinfo=None)
                if entry_time.replace(tzinfo=None) < start_aware:
                    return False
            if end_time:
                end_aware = end_time if end_time.tzinfo else end_time.replace(tzinfo=None)
                if entry_time.replace(tzinfo=None) > end_aware:
                    return False

        # 级别过滤
        if level and entry.level != level:
            return False

        # 事件过滤
        if event and entry.event != event:
            return False

        # 任务过滤
        if task_id and entry.source.get("task_id") != task_id:
            return False

        # 设备过滤
        if device_id and entry.source.get("device_id") != device_id:
            return False

        return True

    def _get_files_in_range(
        self,
        logs_dir: Path,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Path]:
        """获取时间范围内的日志文件"""
        files = []
        current = start_time.date()
        end = end_time.date()

        while current <= end:
            date_str = current.strftime("%Y%m%d")
            pattern = f"client_{self.client_id}_{date_str}.jsonl"
            filepath = logs_dir / pattern

            if filepath.exists():
                files.append(filepath)

            current += timedelta(days=1)

        return files

    # === 清理 ===

    def cleanup_old_logs(self, days: Optional[int] = None) -> int:
        """
        清理过期日志

        Args:
            days: 保留天数

        Returns:
            清理的文件数
        """
        days = days or self.config.retention_days
        cutoff = datetime.now() - timedelta(days=days)
        cleaned = 0

        logs_dir = Path(self.config.local_path)
        if not logs_dir.exists():
            return 0

        for filepath in logs_dir.glob("client_*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
                if mtime < cutoff:
                    filepath.unlink()
                    cleaned += 1
            except Exception as e:
                self._logger.warning(f"Failed to delete old log {filepath}: {e}")

        self._logger.info(f"Cleaned {cleaned} old log files")
        return cleaned

    def get_log_stats(self) -> dict:
        """获取日志统计信息"""
        logs_dir = Path(self.config.local_path)
        total_size = 0
        file_count = 0
        oldest_time = None
        newest_time = None

        if logs_dir.exists():
            for filepath in logs_dir.glob("client_*.jsonl"):
                total_size += filepath.stat().st_size
                file_count += 1

                # 获取文件时间范围
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
                if oldest_time is None or mtime < oldest_time:
                    oldest_time = mtime
                if newest_time is None or mtime > newest_time:
                    newest_time = mtime

        return {
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "file_count": file_count,
            "oldest_log": oldest_time.isoformat() if oldest_time else None,
            "newest_log": newest_time.isoformat() if newest_time else None,
            "retention_days": self.config.retention_days,
        }

    def close(self) -> None:
        """关闭日志系统"""
        # 刷新缓冲区
        with self._lock:
            self._log_buffer.clear()

        # 关闭模块日志器
        for module_logger in self._module_loggers.values():
            for handler in module_logger.handlers[:]:
                handler.close()
                module_logger.removeHandler(handler)
