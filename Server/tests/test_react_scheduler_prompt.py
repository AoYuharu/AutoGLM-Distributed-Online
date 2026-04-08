from src.services.react_scheduler import DeviceTask


def test_device_task_system_prompt_requires_finishing_when_target_already_met():
    task = DeviceTask(
        device_id="device-1",
        task_id="task-finish-prompt",
        instruction="打开设置，把熄屏时间调为10分钟",
    )

    prompt = task.get_system_prompt()

    assert 'do(action="finish", message="任务已完成")' in prompt
    assert "目标值已经等于用户要求的值" in prompt
    assert "不要继续点击、滑动或进入其他页面" in prompt
    assert "只有在屏幕上没有出现明确完成证据时" in prompt
    assert "打开设置，把熄屏时间调为10分钟" in prompt
