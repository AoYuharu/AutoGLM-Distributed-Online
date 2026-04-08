"""
File Storage Manager - Manages device file storage for:
- context.json: Current task context (system_prompt + messages)
- react_records.jsonl: ReAct execution records
- chat_history.json: Chat history (user/assistant/tooluse/system)
- screenshots/: Step screenshots
- logs/: ADB logs
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException


_ALLOWED_DEVICE_SUBDIRS = {"screenshots", "logs"}



import structlog

logger = structlog.get_logger()

# Base storage directory
STORAGE_BASE = Path("./data/storage/devices")


class FileStorageManager:
    """Manages file storage for each device"""

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or STORAGE_BASE

    def _get_device_dir(self, device_id: str) -> Path:
        """Get device storage directory"""
        return self.base_path / device_id

    def _ensure_device_dir(self, device_id: str) -> Path:
        """Ensure device directory exists"""
        device_dir = self._get_device_dir(device_id)
        device_dir.mkdir(parents=True, exist_ok=True)
        return device_dir

    def get_device_dir(self, device_id: str) -> Path:
        """Get device directory path without creating it."""
        return self._get_device_dir(device_id)

    def get_device_file(self, device_id: str, relative_path: str) -> Path:
        """Resolve a device-scoped file path safely."""
        normalized = Path(relative_path)
        if normalized.is_absolute():
            raise HTTPException(status_code=400, detail="Absolute paths are not allowed")
        if any(part == ".." for part in normalized.parts):
            raise HTTPException(status_code=400, detail="Path traversal is not allowed")
        if normalized.parts and normalized.parts[0] not in _ALLOWED_DEVICE_SUBDIRS and normalized.name not in {
            "context.json",
            "chat_history.json",
            "react_records.jsonl",
        }:
            raise HTTPException(status_code=400, detail="Unsupported artifact path")
        return self._get_device_dir(device_id) / normalized

    def get_latest_screenshot_path(self, device_id: str) -> Optional[Path]:
        screenshots_dir = self._get_device_dir(device_id) / "screenshots"
        latest_path = screenshots_dir / "latest.png"
        if latest_path.exists():
            return latest_path
        screenshots = self.get_screenshots(device_id)
        if not screenshots:
            return None
        return screenshots_dir / screenshots[-1]

    def get_log_file_path(self, device_id: str, date: Optional[str] = None) -> Optional[Path]:
        logs_dir = self._get_device_dir(device_id) / "logs"
        if not logs_dir.exists():
            return None
        if date:
            log_file = logs_dir / f"{date}.jsonl"
        else:
            log_file = logs_dir / "latest.jsonl"
        return log_file if log_file.exists() else None

    def get_react_records_file_path(self, device_id: str) -> Optional[Path]:
        path = self._get_device_dir(device_id) / "react_records.jsonl"
        return path if path.exists() else None

    def get_chat_history_file_path(self, device_id: str) -> Optional[Path]:
        path = self._get_device_dir(device_id) / "chat_history.json"
        return path if path.exists() else None

    # ==================== Context Management ====================

    def save_context(self, device_id: str, context: dict) -> None:
        """Save task context to context.json"""
        device_dir = self._ensure_device_dir(device_id)
        context_file = device_dir / "context.json"

        context_data = {
            "system_prompt": context.get("system_prompt", ""),
            "messages": context.get("messages", []),
            "updated_at": datetime.now().isoformat(),
        }

        with open(context_file, "w", encoding="utf-8") as f:
            json.dump(context_data, f, ensure_ascii=False, indent=2)

        logger.debug(f"[file_storage] Context saved for device={device_id}")

    def load_context(self, device_id: str) -> Optional[dict]:
        """Load task context from context.json"""
        context_file = self._get_device_dir(device_id) / "context.json"
        if not context_file.exists():
            return None

        try:
            with open(context_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[file_storage] Failed to load context for device={device_id}: {e}")
            return None

    def clear_context(self, device_id: str) -> None:
        """Clear task context"""
        context_file = self._get_device_dir(device_id) / "context.json"
        if context_file.exists():
            context_file.unlink()
            logger.debug(f"[file_storage] Context cleared for device={device_id}")

    # ==================== Chat History Management ====================

    def save_chat_history(self, device_id: str, messages: list) -> None:
        """Save chat history to chat_history.json"""
        device_dir = self._ensure_device_dir(device_id)
        chat_file = device_dir / "chat_history.json"

        chat_data = {
            "messages": messages,
            "updated_at": datetime.now().isoformat(),
        }

        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(chat_data, f, ensure_ascii=False, indent=2)

        logger.debug(f"[file_storage] Chat history saved for device={device_id}, messages={len(messages)}")

    def load_chat_history(self, device_id: str) -> list:
        """Load chat history from chat_history.json"""
        chat_file = self._get_device_dir(device_id) / "chat_history.json"
        if not chat_file.exists():
            return []

        try:
            with open(chat_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("messages", [])
        except Exception as e:
            logger.error(f"[file_storage] Failed to load chat history for device={device_id}: {e}")
            return []

    def append_chat_message(self, device_id: str, message: dict) -> None:
        """Append a single message to chat history"""
        messages = self.load_chat_history(device_id)
        messages.append(message)
        self.save_chat_history(device_id, messages)

    # ==================== ReAct Records Management ====================

    def append_react_record(self, device_id: str, record: dict) -> None:
        """Append a ReAct record to react_records.jsonl"""
        device_dir = self._ensure_device_dir(device_id)
        records_file = device_dir / "react_records.jsonl"

        record_data = {
            **record,
            "timestamp": datetime.now().isoformat(),
        }

        with open(records_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record_data, ensure_ascii=False) + "\n")

        logger.debug(f"[file_storage] ReAct record appended for device={device_id}, step={record.get('step_number')}")

    def get_react_records(self, device_id: str) -> list:
        """Get all ReAct records from react_records.jsonl"""
        records_file = self._get_device_dir(device_id) / "react_records.jsonl"
        if not records_file.exists():
            return []

        records = []
        try:
            with open(records_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))
        except Exception as e:
            logger.error(f"[file_storage] Failed to load react records for device={device_id}: {e}")

        return records

    # ==================== Screenshot Management ====================

    def save_screenshot(self, device_id: str, step: int, timestamp: str, data: str) -> str:
        """
        Save screenshot to screenshots/ directory.
        Returns the relative path to the screenshot.
        """
        device_dir = self._ensure_device_dir(device_id)
        screenshots_dir = device_dir / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        filename = f"step_{step}_{timestamp}.png"
        filepath = screenshots_dir / filename

        # data is base64 encoded
        if data.startswith("data:image"):
            # Extract base64 data
            import base64
            base64_data = data.split(",")[1] if "," in data else data
            image_data = base64.b64decode(base64_data)
            with open(filepath, "wb") as f:
                f.write(image_data)
        else:
            # Assume raw base64
            import base64
            image_data = base64.b64decode(data)
            with open(filepath, "wb") as f:
                f.write(image_data)

        # Also save as latest.png
        latest_path = screenshots_dir / "latest.png"
        with open(latest_path, "wb") as f:
            f.write(image_data)

        relative_path = f"screenshots/{filename}"
        logger.debug(f"[file_storage] Screenshot saved for device={device_id}, step={step}, path={relative_path}")
        return relative_path

    def get_screenshots(self, device_id: str) -> list:
        """Get list of screenshots for a device"""
        screenshots_dir = self._get_device_dir(device_id) / "screenshots"
        if not screenshots_dir.exists():
            return []

        screenshots = []
        for f in sorted(screenshots_dir.iterdir()):
            if f.suffix == ".png" and f.stem != "latest":
                screenshots.append(f.name)

        return screenshots

    # ==================== ADB Log Management ====================

    def append_adb_log(self, device_id: str, entry: dict) -> None:
        """Append an ADB log entry to logs/{date}.jsonl"""
        device_dir = self._ensure_device_dir(device_id)
        logs_dir = device_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        log_file = logs_dir / f"{today}.jsonl"
        latest_file = logs_dir / "latest.jsonl"

        entry_data = {
            **entry,
            "timestamp": datetime.now().isoformat(),
            "date": today,
        }

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry_data, ensure_ascii=False) + "\n")

        # Also append to latest.jsonl
        with open(latest_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry_data, ensure_ascii=False) + "\n")

        logger.debug(f"[file_storage] ADB log appended for device={device_id}")

    def get_adb_logs(self, device_id: str, date: Optional[str] = None) -> list:
        """Get ADB logs for a specific date or latest logs"""
        logs_dir = self._get_device_dir(device_id) / "logs"
        if not logs_dir.exists():
            return []

        if date:
            log_file = logs_dir / f"{date}.jsonl"
        else:
            log_file = logs_dir / "latest.jsonl"

        if not log_file.exists():
            return []

        logs = []
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        logs.append(json.loads(line))
        except Exception as e:
            logger.error(f"[file_storage] Failed to load ADB logs for device={device_id}: {e}")

        return logs

    # ==================== Cleanup ====================

    def cleanup(self, device_id: str, max_age_days: int = 7) -> None:
        """Clean up old files for a device"""
        device_dir = self._get_device_dir(device_id)
        if not device_dir.exists():
            return

        cutoff_time = time.time() - (max_age_days * 24 * 60 * 60)
        cleaned_files = []

        # Clean logs
        logs_dir = device_dir / "logs"
        if logs_dir.exists():
            for log_file in logs_dir.glob("*.jsonl"):
                if log_file.stem != "latest" and log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
                    cleaned_files.append(str(log_file))

        # Clean old screenshots (keep last 50)
        screenshots_dir = device_dir / "screenshots"
        if screenshots_dir.exists():
            screenshots = sorted(
                [f for f in screenshots_dir.glob("step_*.png")],
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            for old_screenshot in screenshots[50:]:
                old_screenshot.unlink()
                cleaned_files.append(str(old_screenshot))

        if cleaned_files:
            logger.info(f"[file_storage] Cleanup for device={device_id}: removed {len(cleaned_files)} files")


# Global singleton
file_storage = FileStorageManager()
