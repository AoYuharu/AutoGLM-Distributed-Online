"""
Logging configuration for the server
Module-level loggers to avoid circular imports
"""
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.config import settings

# Ensure logs directory exists
Path("./logs").mkdir(exist_ok=True)


def get_log_filename(prefix: str) -> str:
    """Get log filename with current date"""
    today = time.strftime("%Y%m%d")
    return f"./logs/{prefix}_{today}.log"


# Configure standard logging with file handler
file_handler = RotatingFileHandler(
    settings.LOG_FILE,
    maxBytes=settings.LOG_MAX_BYTES,
    backupCount=settings.LOG_BACKUP_COUNT,
)
file_handler.setLevel(getattr(logging, settings.LOG_LEVEL))
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

# Module: API requests
api_file_handler = RotatingFileHandler(
    get_log_filename("server_api"),
    maxBytes=settings.LOG_MAX_BYTES,
    backupCount=settings.LOG_BACKUP_COUNT,
)
api_file_handler.setLevel(logging.INFO)
api_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

# Module: WebSocket messages
ws_file_handler = RotatingFileHandler(
    get_log_filename("server_ws"),
    maxBytes=settings.LOG_MAX_BYTES,
    backupCount=settings.LOG_BACKUP_COUNT,
)
ws_file_handler.setLevel(logging.INFO)
ws_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

# Module: Agent service
agent_file_handler = RotatingFileHandler(
    get_log_filename("server_agent"),
    maxBytes=settings.LOG_MAX_BYTES,
    backupCount=settings.LOG_BACKUP_COUNT,
)
agent_file_handler.setLevel(logging.INFO)
agent_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

# Module: Database operations
db_file_handler = RotatingFileHandler(
    get_log_filename("server_db"),
    maxBytes=settings.LOG_MAX_BYTES,
    backupCount=settings.LOG_BACKUP_COUNT,
)
db_file_handler.setLevel(logging.DEBUG)
db_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

# Module: Network messages (incoming/outgoing)
network_file_handler = RotatingFileHandler(
    get_log_filename("server_network"),
    maxBytes=settings.LOG_MAX_BYTES,
    backupCount=settings.LOG_BACKUP_COUNT,
)
network_file_handler.setLevel(logging.INFO)
network_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

# Module: WebSocket Console (Server -> Web)
ws_console_file_handler = RotatingFileHandler(
    get_log_filename("server_ws_console"),
    maxBytes=settings.LOG_MAX_BYTES,
    backupCount=settings.LOG_BACKUP_COUNT,
)
ws_console_file_handler.setLevel(logging.INFO)
ws_console_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

# Console handler with colored output
console_handler = logging.StreamHandler()
console_handler.setLevel(getattr(logging, settings.LOG_LEVEL))
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

# Get loggers for each module
api_logger = logging.getLogger("server.api")
api_logger.addHandler(api_file_handler)
api_logger.addHandler(console_handler)
api_logger.setLevel(logging.INFO)

ws_logger = logging.getLogger("server.ws")
ws_logger.addHandler(ws_file_handler)
ws_logger.addHandler(console_handler)
ws_logger.setLevel(logging.INFO)

agent_logger = logging.getLogger("server.agent")
agent_logger.addHandler(agent_file_handler)
agent_logger.addHandler(console_handler)
agent_logger.setLevel(logging.INFO)

db_logger = logging.getLogger("server.db")
db_logger.addHandler(db_file_handler)
db_logger.addHandler(console_handler)
db_logger.setLevel(logging.DEBUG)

network_logger = logging.getLogger("server.network")
network_logger.addHandler(network_file_handler)
network_logger.addHandler(console_handler)
network_logger.setLevel(logging.INFO)

ws_console_logger = logging.getLogger("server.ws_console")
ws_console_logger.addHandler(ws_console_file_handler)
ws_console_logger.addHandler(console_handler)
ws_console_logger.setLevel(logging.INFO)


def get_api_logger() -> logging.Logger:
    """Get the API logger instance"""
    return api_logger


def get_ws_logger() -> logging.Logger:
    """Get the WebSocket logger instance"""
    return ws_logger


def get_agent_logger() -> logging.Logger:
    """Get the Agent logger instance"""
    return agent_logger


def get_db_logger() -> logging.Logger:
    """Get the Database logger instance"""
    return db_logger


def get_network_logger() -> logging.Logger:
    """Get the Network messages logger instance"""
    return network_logger


def get_ws_console_logger() -> logging.Logger:
    """Get the WebSocket Console logger instance (Server -> Web)"""
    return ws_console_logger