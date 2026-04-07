"""
ActionInterpreter 单元测试
"""
import pytest
from src.executor.interpreter import ActionInterpreter, do, finish


class TestActionInterpreter:
    """动作解释器测试"""

    def setup_method(self):
        """每个测试前初始化"""
        self.interpreter = ActionInterpreter()

    def test_parse_dict_do_action(self):
        """解析字典格式的 do 动作"""
        response = {
            "_metadata": "do",
            "action": "Tap",
            "element": {"x": 500, "y": 300}
        }
        result = self.interpreter.parse(response)
        assert result["_metadata"] == "do"
        assert result["action"] == "Tap"
        assert result["element"] == {"x": 500, "y": 300}

    def test_parse_dict_finish(self):
        """解析字典格式的 finish"""
        response = {
            "_metadata": "finish",
            "message": "任务完成"
        }
        result = self.interpreter.parse(response)
        assert result["_metadata"] == "finish"
        assert result["message"] == "任务完成"

    def test_parse_string_do_action(self):
        """解析字符串格式的 do 动作"""
        response = 'do(action="Tap", element={"x": 500, "y": 300})'
        result = self.interpreter.parse(response)
        assert result["_metadata"] == "do"
        assert result["action"] == "Tap"
        assert result["element"] == {"x": 500, "y": 300}

    def test_parse_string_type_action(self):
        """解析字符串格式的 Type 动作"""
        response = 'do(action="Type", text="hello world")'
        result = self.interpreter.parse(response)
        assert result["_metadata"] == "do"
        assert result["action"] == "Type"
        assert result["text"] == "hello world"

    def test_parse_string_launch_action(self):
        """解析字符串格式的 Launch 动作"""
        response = 'do(action="Launch", app="微信")'
        result = self.interpreter.parse(response)
        assert result["_metadata"] == "do"
        assert result["action"] == "Launch"
        assert result["app"] == "微信"

    def test_parse_string_swipe_action(self):
        """解析字符串格式的 Swipe 动作"""
        response = 'do(action="Swipe", start={"x": 500, "y": 800}, end={"x": 500, "y": 400})'
        result = self.interpreter.parse(response)
        assert result["_metadata"] == "do"
        assert result["action"] == "Swipe"
        assert result["start"] == {"x": 500, "y": 800}
        assert result["end"] == {"x": 500, "y": 400}

    def test_parse_string_finish(self):
        """解析字符串格式的 finish"""
        response = 'finish(message="任务完成")'
        result = self.interpreter.parse(response)
        assert result["_metadata"] == "finish"
        assert "任务完成" in result["message"]

    def test_parse_json_string(self):
        """解析 JSON 字符串"""
        response = '{"_metadata": "do", "action": "Back"}'
        result = self.interpreter.parse(response)
        assert result["_metadata"] == "do"
        assert result["action"] == "Back"

    def test_parse_unknown_response(self):
        """解析未知格式"""
        response = "some unknown format"
        result = self.interpreter.parse(response)
        assert result["_metadata"] == "do"
        assert result["action"] == "unknown"
        assert result["raw"] == response


class TestHelperFunctions:
    """辅助函数测试"""

    def test_do_function(self):
        """do 辅助函数"""
        result = do(action="Tap", element={"x": 100, "y": 200})
        assert result["_metadata"] == "do"
        assert result["action"] == "Tap"
        assert result["element"] == {"x": 100, "y": 200}

    def test_finish_function(self):
        """finish 辅助函数"""
        result = finish(message="完成")
        assert result["_metadata"] == "finish"
        assert result["message"] == "完成"

    def test_finish_default_message(self):
        """finish 默认消息"""
        result = finish()
        assert result["_metadata"] == "finish"
        assert result["message"] == ""
