"""
ReAct 类型定义 - 枚举和数据类
"""
from enum import Enum
from typing import Optional


class SessionStatus(Enum):
    """会话状态"""
    WAIT_FOR_PUSH = "wait_for_push"       # 等待推理
    WAIT_OBSERVATION = "wait_observation"  # 等待观察结果
    WAIT_USER_DECISION = "wait_user_decision"  # 等待用户决策
    FINISHED = "finished"                  # 任务结束


class ReActErrorType(Enum):
    """ReAct 错误类型"""
    REMOTE_API_TIMEOUT = "remote_api_timeout"
    REMOTE_API_RETRIES_EXCEEDED = "remote_api_retries_exceeded"
    ACTION_PARSE_FAILED = "action_parse_failed"
    ACTION_RECONSTRUCT_EXCEEDED = "action_reconstruct_exceeded"
    DEVICE_STATUS_UNEXPECTED = "device_status_unexpected"
    ACK_TIMEOUT = "ack_timeout"
    ACK_REJECTED = "ack_rejected"
    OBSERVE_TIMEOUT = "observe_timeout"
    OBSERVE_ERROR = "observe_error"
    DEVICE_DISCONNECTED = "device_disconnected"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
