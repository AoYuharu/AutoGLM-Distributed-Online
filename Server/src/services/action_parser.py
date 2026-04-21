"""
Action 解析器 - 解析和验证 AI 模型输出的 action
"""
import asyncio
import json
import re
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from openai import OpenAI

from src.config import settings
from src.services.react_types import ReActErrorType

if TYPE_CHECKING:
    pass


VALID_ACTIONS = {
    "Launch",
    "Tap",
    "Swipe",
    "Type",
    "Back",
    "Home",
    "Wait",
    "Long_Press",
    "Long_Press",
    "Double_Tap",
    "finish",
    # 额外支持的变体
    "launch",
    "tap",
    "swipe",
    "type",
    "back",
    "home",
    "wait",
    "long_press",
    "double_tap",
    "Long_Press",
    "Double_Tap",
}


@dataclass
class ActionParseResult:
    """Action 解析结果"""
    success: bool
    action: Optional[dict]
    error: Optional[str]
    error_type: Optional[ReActErrorType]
    attempts: int


class ActionParser:
    """Action 解析器，支持自重构"""

    MAX_RECONSTRUCT_ATTEMPTS = 3

    def __init__(self, model_client: Optional[OpenAI] = None):
        self._model_client = model_client

    @property
    def model_client(self) -> OpenAI:
        if self._model_client is None:
            import httpx
            import os
            proxies = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            if not proxies:
                try:
                    import winreg
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
                    proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
                    if proxy_enable:
                        proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
                        proxies = f"http://{proxy_server}"
                except Exception:
                    pass
            if proxies:
                self._model_client = OpenAI(
                    base_url=settings.PHONE_AGENT_BASE_URL,
                    api_key=settings.PHONE_AGENT_API_KEY,
                    http_client=httpx.Client(proxy=proxies),
                )
            else:
                self._model_client = OpenAI(
                    base_url=settings.PHONE_AGENT_BASE_URL,
                    api_key=settings.PHONE_AGENT_API_KEY,
                    http_client=httpx.Client(trust_env=False),
                )
        return self._model_client

    async def parse_and_validate(
        self,
        reasoning: str,
        raw_model_output: str,
        device_type: str = "android",
        attempt: int = 1,
    ) -> ActionParseResult:
        """
        解析和验证 action

        1. 从模型输出中提取 action
        2. 校验是否在有效动作集合中
        3. 若无效且 attempt < 3: 调用自重构
        """
        # 解析 action
        action = await self._parse_action_from_output(raw_model_output)

        if action is None:
            if attempt < self.MAX_RECONSTRUCT_ATTEMPTS:
                return await self._reconstruct_action(
                    bad_output=raw_model_output,
                    error_msg="无法从模型输出中解析出 action",
                    reasoning=reasoning,
                    device_type=device_type,
                    attempt=attempt,
                )
            return ActionParseResult(
                success=False,
                action=None,
                error=f"无法从模型输出中解析出 action (attempt {attempt})",
                error_type=ReActErrorType.ACTION_PARSE_FAILED,
                attempts=attempt,
            )

        # 验证 action 类型
        action_type = action.get("action", "").lower()
        normalized_type = self._normalize_action_type(action_type)

        if normalized_type not in {a.lower() for a in VALID_ACTIONS}:
            if attempt < self.MAX_RECONSTRUCT_ATTEMPTS:
                return await self._reconstruct_action(
                    bad_output=raw_model_output,
                    error_msg=f"无效的 action 类型: {action_type}，有效类型: {', '.join(sorted(VALID_ACTIONS))}",
                    reasoning=reasoning,
                    device_type=device_type,
                    attempt=attempt,
                )
            return ActionParseResult(
                success=False,
                action=None,
                error=f"无效的 action 类型: {action_type} (attempt {attempt})",
                error_type=ReActErrorType.ACTION_RECONSTRUCT_EXCEEDED,
                attempts=attempt,
            )

        return ActionParseResult(
            success=True,
            action=action,
            error=None,
            error_type=None,
            attempts=attempt,
        )

    def _normalize_action_type(self, action_type: str) -> str:
        """标准化 action 类型"""
        type_map = {
            "long_press": "long_press",
            "longpress": "long_press",
            "long press": "long_press",
            "double_tap": "double_tap",
            "doubletap": "double_tap",
            "double tap": "double_tap",
            "finish": "finish",
            "stop": "finish",
            "done": "finish",
        }
        return type_map.get(action_type.lower(), action_type.lower())

    async def _parse_action_from_output(self, content: str) -> Optional[dict]:
        """从模型输出中解析 action"""
        # 提取 <answer>...</answer> 中的内容
        if "<answer>" in content:
            parts = content.split("<answer>")
            if len(parts) > 1:
                action_text = parts[1].split("</answer>")[0].strip()
                return self._parse_action_text(action_text)

        # 提取 do(action=...) 格式
        if "do(action=" in content:
            idx = content.index("do(action=")
            action_text = content[idx:]
            match = re.search(r"do\([^)]+\)", action_text)
            if match:
                return self._parse_action_text(match.group(0))

        # 提取 JSON 格式
        if "{" in content:
            try:
                start = content.index("{")
                end = content.rindex("}") + 1
                return json.loads(content[start:end])
            except (json.JSONDecodeError, ValueError):
                pass

        # 尝试直接解析整个内容
        return self._parse_action_text(content.strip())

    def _parse_action_text(self, action_text: str) -> Optional[dict]:
        """将动作文本解析为字典"""
        action_text = action_text.strip()

        # 解析 do(action="Type", ...) 格式
        if "do(action=" in action_text:
            try:
                match = re.search(r"do\(action\s*=\s*[\"']([^\"']+)[\"']", action_text)
                if match:
                    action_type = match.group(1)
                    params = {}

                    element_match = re.search(r"element\s*=\s*\[(\d+),\s*(\d+)\]", action_text)
                    if element_match:
                        params["x"] = int(element_match.group(1))
                        params["y"] = int(element_match.group(2))

                    start_match = re.search(r"start\s*=\s*\[(\d+),\s*(\d+)\]", action_text)
                    end_match = re.search(r"end\s*=\s*\[(\d+),\s*(\d+)\]", action_text)
                    if start_match:
                        params["x1"] = int(start_match.group(1))
                        params["y1"] = int(start_match.group(2))
                    if end_match:
                        params["x2"] = int(end_match.group(1))
                        params["y2"] = int(end_match.group(2))

                    text_match = re.search(r'text\s*=\s*["\']([^"\']+)["\']', action_text)
                    if text_match:
                        params["text"] = text_match.group(1)

                    app_match = re.search(r'app\s*=\s*["\']([^"\']+)["\']', action_text)
                    if app_match:
                        params["app"] = app_match.group(1)

                    duration_match = re.search(r'duration\s*=\s*["\']?(\d+)\s*(?:seconds?)?["\']?', action_text, re.I)
                    if duration_match:
                        params["duration"] = int(duration_match.group(1))

                    return {"action": action_type, **params}
            except Exception:
                pass

        # 解析纯 JSON 格式
        if "{" in action_text:
            try:
                start = action_text.index("{")
                end = action_text.rindex("}") + 1
                return json.loads(action_text[start:end])
            except (json.JSONDecodeError, ValueError):
                pass

        # 关键字匹配
        action_lower = action_text.lower()
        if "finish" in action_lower:
            return {"action": "finish"}
        elif "launch" in action_lower:
            return {"action": "Launch"}
        elif "long press" in action_lower:
            elem_match = re.search(r"\[(\d+),\s*(\d+)\]", action_text)
            params = {}
            if elem_match:
                params["x"] = int(elem_match.group(1))
                params["y"] = int(elem_match.group(2))
            return {"action": "Long_Press", **params}
        elif "double tap" in action_lower:
            elem_match = re.search(r"\[(\d+),\s*(\d+)\]", action_text)
            params = {}
            if elem_match:
                params["x"] = int(elem_match.group(1))
                params["y"] = int(elem_match.group(2))
            return {"action": "Double_Tap", **params}
        elif "tap" in action_lower:
            elem_match = re.search(r"\[(\d+),\s*(\d+)\]", action_text)
            params = {}
            if elem_match:
                params["x"] = int(elem_match.group(1))
                params["y"] = int(elem_match.group(2))
            return {"action": "Tap", **params}
        elif "swipe" in action_lower:
            start_match = re.search(r"start\s*=\s*\[(\d+),\s*(\d+)\]", action_text)
            end_match = re.search(r"end\s*=\s*\[(\d+),\s*(\d+)\]", action_text)
            params = {}
            if start_match:
                params["x1"] = int(start_match.group(1))
                params["y1"] = int(start_match.group(2))
            if end_match:
                params["x2"] = int(end_match.group(1))
                params["y2"] = int(end_match.group(2))
            return {"action": "Swipe", **params}
        elif "type" in action_lower:
            text_match = re.search(r'text\s*=\s*["\']([^"\']+)["\']', action_text)
            params = {}
            if text_match:
                params["text"] = text_match.group(1)
            return {"action": "Type", **params}
        elif "back" in action_lower:
            return {"action": "Back"}
        elif "home" in action_lower:
            return {"action": "Home"}
        elif "wait" in action_lower:
            dur_match = re.search(r"(\d+)", action_text)
            params = {}
            if dur_match:
                params["duration"] = int(dur_match.group(1))
            return {"action": "Wait", **params}

        return None

    async def _reconstruct_action(
        self,
        bad_output: str,
        error_msg: str,
        reasoning: str,
        device_type: str,
        attempt: int,
    ) -> ActionParseResult:
        """调用 AI 生成正确的 action"""
        from src.services.react_types import ReActErrorType

        reconstruct_prompt = f"""你是一个动作解析器。上一个动作解析失败了。

原始 AI 输出:
{bad_output[:500]}

错误信息:
{error_msg}

有效的动作类型:
{', '.join(sorted(VALID_ACTIONS))}

请根据以下推理内容，生成一个正确的动作:

推理内容:
{reasoning[:500] if reasoning else '无'}

请只输出一个有效的动作，使用以下格式之一:
1. do(action="动作类型", 参数...)
2. 或直接的 JSON 格式

不要有任何其他解释或说明。"""

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.model_client.chat.completions.create,
                    model=settings.PHONE_AGENT_MODEL,
                    messages=[
                        {"role": "system", "content": "你是一个动作解析器。请根据给定的推理内容生成正确的动作。"},
                        {"role": "user", "content": reconstruct_prompt},
                    ],
                    max_tokens=256,
                    temperature=0.1,
                ),
                timeout=10.0,
            )

            new_output = response.choices[0].message.content
            return await self.parse_and_validate(
                reasoning=reasoning,
                raw_model_output=new_output,
                device_type=device_type,
                attempt=attempt + 1,
            )

        except asyncio.TimeoutError:
            return ActionParseResult(
                success=False,
                action=None,
                error=f"动作重构超时 (attempt {attempt})",
                error_type=ReActErrorType.ACTION_RECONSTRUCT_EXCEEDED,
                attempts=attempt,
            )
        except Exception as e:
            return ActionParseResult(
                success=False,
                action=None,
                error=f"动作重构失败: {str(e)} (attempt {attempt})",
                error_type=ReActErrorType.ACTION_RECONSTRUCT_EXCEEDED,
                attempts=attempt,
            )
