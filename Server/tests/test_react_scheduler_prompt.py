import pytest

from src.services.react_scheduler import DeviceTask, ReActRecord


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


def _make_task() -> DeviceTask:
    task = DeviceTask(device_id="device-1", task_id="task-1", instruction="打开设置")
    task.initialize()
    return task


def test_reason_prompt_uses_single_image_when_only_latest_screenshot_exists():
    task = _make_task()
    task.initial_screenshot = "single-image"

    prompt = task.get_reason_prompt_for_tests()
    image_parts = [part for part in prompt["content"] if part["type"] == "image_url"]

    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == "data:image/png;base64,single-image"
    assert task.get_reason_prompt_state_for_tests()["mode"] == "single"


def test_reason_prompt_uses_before_and_after_images_in_order():
    task = _make_task()
    task.react_records.append(
        ReActRecord(
            step_number=1,
            before_action_screenshot="before-image",
            screenshot="after-image",
            screenshot_unchanged=False,
        )
    )

    prompt = task.get_reason_prompt_for_tests()
    image_parts = [part for part in prompt["content"] if part["type"] == "image_url"]

    assert len(image_parts) == 2
    assert image_parts[0]["image_url"]["url"] == "data:image/png;base64,before-image"
    assert image_parts[1]["image_url"]["url"] == "data:image/png;base64,after-image"
    assert task.get_reason_prompt_state_for_tests()["mode"] == "comparison"


def test_reason_prompt_warns_when_previous_action_had_no_visual_effect():
    task = _make_task()
    task.react_records.append(
        ReActRecord(
            step_number=1,
            before_action_screenshot="before-image",
            screenshot="after-image",
            screenshot_unchanged=True,
        )
    )

    prompt_text = task.get_reason_prompt_text_for_tests()

    assert "最新观察截图与上一步动作执行前截图完全一致" in prompt_text
    assert "应视为上一条操作未生效" in prompt_text
    assert task.get_reason_prompt_warning_for_tests() is True


@pytest.mark.parametrize(
    "before_screenshot,after_screenshot",
    [
        ("before-image", ""),
        ("", "after-image"),
    ],
)
def test_reason_prompt_falls_back_to_single_image_without_full_pair(before_screenshot: str, after_screenshot: str):
    task = _make_task()
    task.initial_screenshot = "latest-image"
    task.react_records.append(
        ReActRecord(
            step_number=1,
            before_action_screenshot=before_screenshot,
            screenshot=after_screenshot,
        )
    )

    prompt = task.get_reason_prompt_for_tests()
    image_parts = [part for part in prompt["content"] if part["type"] == "image_url"]

    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == f"data:image/png;base64,{after_screenshot or 'latest-image'}"
    assert task.get_reason_prompt_state_for_tests()["mode"] == "single"
