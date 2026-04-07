"""
HTTP 客户端封装

用于发送 device_status 和 observe_result 等消息到 Server
"""
import asyncio
import logging
from typing import Optional, Any
from datetime import datetime
import uuid

import aiohttp

logger = logging.getLogger(__name__)


class HttpClient:
    """
    HTTP 客户端

    提供 HTTP POST 方法用于发送大数据消息
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        timeout: float = 30.0,
    ):
        """
        初始化 HTTP 客户端

        Args:
            base_url: Server 基础 URL (如 http://localhost:8000)
            client_id: 客户端 ID
            timeout: 请求超时时间（秒）
        """
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 HTTP Session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """关闭 HTTP Session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _create_headers(self, msg_id: str) -> dict:
        """创建请求头"""
        return {
            "Content-Type": "application/json",
            "X-Client-ID": self.client_id,
            "X-Message-ID": msg_id,
        }

    async def post_json(
        self,
        endpoint: str,
        data: dict,
        wait_response: bool = True,
    ) -> Optional[dict]:
        """
        发送 JSON POST 请求

        Args:
            endpoint: API 端点 (如 /api/v1/devices/status)
            data: 请求数据
            wait_response: 是否等待响应

        Returns:
            响应数据，如果 wait_response=False 则返回 None
        """
        msg_id = data.get("msg_id") or str(uuid.uuid4())
        data["msg_id"] = msg_id
        data["timestamp"] = datetime.now().isoformat() + "Z"
        data["client_id"] = self.client_id


        url = f"{self.base_url}{endpoint}"

        try:
            session = await self._get_session()
            msg_type = data.get("type", "unknown")
            logger.debug(f"[post_json] Posting to {url}: type={msg_type}, msg_id={msg_id}")

            # 网络消息归档日志
            logger.info(f"[network_outgoing] HTTP POST to {url}: type={msg_type}, msg_id={msg_id}")

            async with session.post(
                url,
                json=data,
                headers=self._create_headers(msg_id),
            ) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    logger.error(f"[post_json] HTTP {response.status} from {url}: {error_text}")
                    return None

                if wait_response:
                    result = await response.json()
                    logger.debug(f"[post_json] Response: {result}")
                    return result
                else:
                    return {"success": True}

        except asyncio.TimeoutError:
            logger.error(f"[post_json] Timeout posting to {url}")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"[post_json] Client error posting to {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"[post_json] Error posting to {url}: {e}")
            return None

    # === 特定消息发送方法 ===

    async def send_device_status(self, devices: list[dict]) -> Optional[dict]:
        """
        发送设备状态 (HTTP POST)

        Args:
            devices: 设备状态列表

        Returns:
            响应数据
        """
        # Debug: log each device's device_name
        for d in devices:
            logger.info(f"[send_device_status] device_id={d.get('device_id')}, device_name={d.get('device_name')}")
        data = {
            "type": "device_status",
            "version": "1.0",
            "payload": {
                "devices": devices,
            }
        }
        return await self.post_json("/api/v1/devices/status", data)

    async def send_observe_result(
        self,
        task_id: str,
        device_id: str,
        step_number: int,
        screenshot: Optional[str] = None,
        result: str = "",
        success: bool = True,
        error: Optional[str] = None,
        version: Optional[int] = None,
    ) -> Optional[dict]:
        """
        发送观察结果 (HTTP POST)

        Args:
            task_id: 任务 ID
            device_id: 设备 ID
            step_number: 步骤编号
            screenshot: 截图 (base64 编码)
            result: 执行结果描述
            success: 是否成功
            error: 错误信息

        Returns:
            响应数据
        """
        data = {
            "type": "observe_result",
            "version": "1.0",
            "payload": {
                "task_id": task_id,
                "device_id": device_id,
                "step_number": step_number,
                "screenshot": screenshot,
                "result": result,
                "success": success,
                "error": error,
            }
        }
        if version is not None:
            data["version"] = str(version)
            data["payload"]["version"] = version
        return await self.post_json("/api/v1/tasks/observe", data)

    async def send_device_offline(self, device_id: str) -> Optional[dict]:
        """
        报告设备离线到 Server

        Args:
            device_id: 设备 ID

        Returns:
            响应数据
        """
        data = {
            "type": "device_offline",
            "version": "1.0",
            "payload": {
                "device_id": device_id,
            }
        }
        return await self.post_json("/api/v1/devices/offline", data)
