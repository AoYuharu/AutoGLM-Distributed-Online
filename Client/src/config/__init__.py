"""
Client runtime configuration loader.

Reads config/client.yaml and provides a dataclass of runtime defaults.
YAML is optional — if absent or unreadable, defaults are returned silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


@dataclass
class ClientRuntimeConfig:
    server_ws_url: str = "ws://localhost:8000"
    server_http_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    polling_interval: float = 3.0
    ws_max_reconnect_attempts: int = 10
    ws_reconnect_base_delay: float = 1.0
    ws_reconnect_max_delay: float = 60.0
    ws_send_ack_timeout: float = 10.0
    http_timeout: float = 30.0
    http_observe_retry_attempts: int = 1
    adb_enabled: bool = True
    adb_binary: str = "adb"
    hdc_enabled: bool = False
    hdc_binary: str = "hdc"
    wda_enabled: bool = False
    wda_url: str = "http://localhost:8100"
    wda_session_timeout: int = 300


def _find_config_file() -> Optional[Path]:
    """Search upward from this file for config/client.yaml."""
    current = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = current.parent / "config" / "client.yaml"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def load_client_runtime_config(config_path: Optional[str] = None) -> ClientRuntimeConfig:
    """
    Load Client runtime config from YAML.

    Search order:
    1. Explicit ``config_path`` argument (--config CLI flag).
    2. ``config/client.yaml`` relative to the Client/ root directory.
    3. Fall back to hard defaults.
    """
    cfg = ClientRuntimeConfig()

    if yaml is None:
        return cfg

    path: Optional[Path] = None
    if config_path:
        path = Path(config_path)
    else:
        path = _find_config_file()

    if path and path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            c = raw.get("client", raw)
            if not isinstance(c, dict):
                c = {}

            def g(key: str, default):
                val = c.get(key)
                return val if val is not None else default

            cfg.server_ws_url = g("server_ws_url", cfg.server_ws_url)
            cfg.server_http_base_url = g("server_http_base_url", cfg.server_http_base_url)
            cfg.log_level = g("log_level", cfg.log_level)
            cfg.polling_interval = float(g("polling_interval", cfg.polling_interval))

            ws = c.get("websocket", {})
            if isinstance(ws, dict):
                cfg.ws_max_reconnect_attempts = int(ws.get("max_reconnect_attempts", cfg.ws_max_reconnect_attempts))
                cfg.ws_reconnect_base_delay = float(ws.get("reconnect_base_delay", cfg.ws_reconnect_base_delay))
                cfg.ws_reconnect_max_delay = float(ws.get("reconnect_max_delay", cfg.ws_reconnect_max_delay))
                cfg.ws_send_ack_timeout = float(ws.get("send_ack_timeout", cfg.ws_send_ack_timeout))

            http = c.get("http", {})
            if isinstance(http, dict):
                cfg.http_timeout = float(http.get("timeout", cfg.http_timeout))
                cfg.http_observe_retry_attempts = int(http.get("observe_retry_attempts", cfg.http_observe_retry_attempts))

            platforms = c.get("platforms", {})
            if isinstance(platforms, dict):
                adb_cfg = platforms.get("adb", {})
                if isinstance(adb_cfg, dict):
                    if adb_cfg.get("enabled") is not None:
                        cfg.adb_enabled = bool(adb_cfg["enabled"])
                    if adb_cfg.get("binary") is not None:
                        cfg.adb_binary = str(adb_cfg["binary"])

                hdc_cfg = platforms.get("hdc", {})
                if isinstance(hdc_cfg, dict):
                    if hdc_cfg.get("enabled") is not None:
                        cfg.hdc_enabled = bool(hdc_cfg["enabled"])
                    if hdc_cfg.get("binary") is not None:
                        cfg.hdc_binary = str(hdc_cfg["binary"])

                wda_cfg = platforms.get("wda", {})
                if isinstance(wda_cfg, dict):
                    if wda_cfg.get("enabled") is not None:
                        cfg.wda_enabled = bool(wda_cfg["enabled"])
                    if wda_cfg.get("default_url") is not None:
                        cfg.wda_url = str(wda_cfg["default_url"])
                    if wda_cfg.get("session_timeout") is not None:
                        cfg.wda_session_timeout = int(wda_cfg["session_timeout"])

        except Exception:
            pass  # degrade to defaults silently

    return cfg


def merge_cli_overrides(cfg: ClientRuntimeConfig, cli_args) -> ClientRuntimeConfig:
    """
    Apply CLI argument overrides onto a loaded config.

    The CLI always wins over YAML.  ``None``-valued CLI defaults are skipped
    so that YAML values are preserved when the user doesn't explicitly pass
    a flag.
    """
    if getattr(cli_args, "server", None) is not None:
        cfg.server_ws_url = cli_args.server
    if getattr(cli_args, "log_level", None) is not None:
        cfg.log_level = cli_args.log_level
    if getattr(cli_args, "enable_adb", None) is not None:
        cfg.adb_enabled = cli_args.enable_adb
    if getattr(cli_args, "enable_hdc", None) is not None:
        cfg.hdc_enabled = cli_args.enable_hdc
    if getattr(cli_args, "enable_wda", None) is not None:
        cfg.wda_enabled = cli_args.enable_wda
    return cfg
