import asyncio
import base64

import pytest

from src.services.react_scheduler import DeviceTask, ObserveException, ReActScheduler, TaskStatus
from src.services.react_types import ReActErrorType


@pytest.fixture()
def scheduler_instance():
    scheduler = ReActScheduler(core_threads=1, max_threads=1)
    yield scheduler
    scheduler.executor.shutdown(wait=False)


def _make_task(device_id: str, task_id: str) -> DeviceTask:
    task = DeviceTask(device_id=device_id, task_id=task_id, instruction=f"instruction for {task_id}")
    task.initialize()
    return task


def _register_task(scheduler: ReActScheduler, task: DeviceTask, *, queue_entries: list[str] | None = None):
    with scheduler._queue_lock:
        scheduler._device_tasks[task.device_id] = task
        if queue_entries is not None:
            scheduler._task_queue.extend(queue_entries)


def test_duplicate_device_queue_entry_acquires_only_once(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task, queue_entries=["device-1", "device-1"])

    acquired = scheduler_instance.get_next_task()

    assert acquired is not None
    acquired_task, execution_token = acquired
    assert acquired_task is task
    assert scheduler_instance._device_execution_tokens["device-1"] == execution_token
    assert scheduler_instance.get_next_task() is None
    assert scheduler_instance._task_queue == []


def test_requeue_skips_stale_worker_after_remove(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task, queue_entries=["device-1"])

    acquired = scheduler_instance.get_next_task()
    assert acquired is not None
    acquired_task, execution_token = acquired

    scheduler_instance.remove_task("device-1")

    requeued = scheduler_instance.requeue_task("device-1", acquired_task, execution_token)

    assert requeued is False
    assert scheduler_instance.get_task("device-1") is None
    assert "device-1" not in scheduler_instance._task_queue
    assert "device-1" not in scheduler_instance._device_execution_tokens


@pytest.mark.asyncio
async def test_replaced_task_blocks_stale_worker_finalize(scheduler_instance: ReActScheduler, monkeypatch):
    old_task = _make_task("device-1", "task-1")
    new_task = _make_task("device-1", "task-2")
    _register_task(scheduler_instance, old_task, queue_entries=["device-1"])

    acquired = scheduler_instance.get_next_task()
    assert acquired is not None
    _, execution_token = acquired

    complete_calls = []
    failed_calls = []
    dispatch_calls = []

    async def fake_emit_phase_start(*args, **kwargs):
        return None

    async def fake_reason_with_retry(task):
        with scheduler_instance._queue_lock:
            scheduler_instance._device_tasks[task.device_id] = new_task
        return "done", {"action": "finish"}, '<answer>do(action="finish")</answer>'

    async def fake_emit_complete(task, final_reasoning):
        complete_calls.append((task.task_id, final_reasoning))

    async def fake_emit_failed(task, message, error_type, final_reasoning=""):
        failed_calls.append((task.task_id, message, error_type, final_reasoning))

    async def fake_dispatch_with_retry(*args, **kwargs):
        dispatch_calls.append((args, kwargs))
        return {"result": "should-not-run"}

    async def fake_ensure_bootstrap_observation(*args, **kwargs):
        return True

    monkeypatch.setattr(scheduler_instance, "_ensure_bootstrap_observation", fake_ensure_bootstrap_observation)
    monkeypatch.setattr(scheduler_instance, "_emit_phase_start", fake_emit_phase_start)
    monkeypatch.setattr(scheduler_instance, "_reason_with_retry", fake_reason_with_retry)
    monkeypatch.setattr(scheduler_instance, "_emit_complete", fake_emit_complete)
    monkeypatch.setattr(scheduler_instance, "_emit_failed", fake_emit_failed)
    monkeypatch.setattr(scheduler_instance, "_dispatch_with_retry", fake_dispatch_with_retry)

    result = await scheduler_instance.run_one_cycle("device-1", old_task, execution_token)

    assert result is True
    assert complete_calls == []
    assert failed_calls == []
    assert dispatch_calls == []
    assert scheduler_instance.get_task("device-1") is new_task
    assert new_task.status == TaskStatus.PENDING
    scheduler_instance._release_execution_token("device-1", execution_token)


def test_requeue_deduplicates_queue_entries(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task, queue_entries=["device-1"])

    acquired = scheduler_instance.get_next_task()
    assert acquired is not None
    acquired_task, execution_token = acquired

    first = scheduler_instance.requeue_task("device-1", acquired_task, execution_token)
    second = scheduler_instance.requeue_task("device-1", acquired_task, execution_token)

    assert first is True
    assert second is False
    assert scheduler_instance._task_queue.count("device-1") == 1


def test_worker_requeues_replacement_task_after_old_owner_exits(scheduler_instance: ReActScheduler, monkeypatch):
    old_task = _make_task("device-1", "task-1")
    new_task = _make_task("device-1", "task-2")
    _register_task(scheduler_instance, old_task, queue_entries=["device-1"])

    async def fake_run_one_cycle(device_id, task, execution_token):
        with scheduler_instance._queue_lock:
            scheduler_instance._device_tasks[device_id] = new_task
        scheduler_instance._running = False
        return True

    monkeypatch.setattr(scheduler_instance, "run_one_cycle", fake_run_one_cycle)

    scheduler_instance._running = True
    scheduler_instance._worker_loop(0)

    assert scheduler_instance.get_task("device-1") is new_task
    assert new_task.status == TaskStatus.PENDING
    assert scheduler_instance._task_queue == ["device-1"]
    assert "device-1" not in scheduler_instance._device_execution_tokens


@pytest.mark.asyncio
async def test_set_observe_result_consumes_bootstrap_waiter_for_step_zero(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task)

    waiter = asyncio.get_running_loop().create_future()
    scheduler_instance._bootstrap_waiters["device-1"] = waiter

    await scheduler_instance.set_observe_result(
        "device-1",
        "bootstrap-image",
        "screenshot_captured",
        step_number=0,
        success=True,
    )

    assert waiter.done()
    assert waiter.result() == ("bootstrap-image", "screenshot_captured")


@pytest.mark.asyncio
async def test_set_observe_result_step_zero_stores_bootstrap_state_on_active_task(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task)

    await scheduler_instance.set_observe_result(
        "device-1",
        "bootstrap-image",
        "screenshot_captured",
        step_number=0,
        success=True,
    )

    assert task.initial_screenshot == "bootstrap-image"
    assert task.initial_observation == "screenshot_captured"
    assert task.initial_observe_success is True


@pytest.mark.asyncio
async def test_set_observe_result_fails_bootstrap_waiter_on_error(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task)

    waiter = asyncio.get_running_loop().create_future()
    scheduler_instance._bootstrap_waiters["device-1"] = waiter

    await scheduler_instance.set_observe_result(
        "device-1",
        "",
        "bootstrap failed",
        step_number=0,
        success=False,
        error="bootstrap failed",
    )

    assert waiter.done()
    with pytest.raises(Exception) as exc_info:
        waiter.result()
    assert "bootstrap failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_set_observe_result_step_zero_without_waiter_still_updates_bootstrap_state(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task)

    await scheduler_instance.set_observe_result(
        "device-1",
        "bootstrap-image",
        "screenshot_captured",
        step_number=0,
        success=True,
    )

    assert scheduler_instance._bootstrap_waiters == {}
    assert task.initial_screenshot == "bootstrap-image"
    assert task.initial_observation == "screenshot_captured"
    assert task.initial_observe_success is True


@pytest.mark.asyncio
async def test_handle_bootstrap_ack_resolves_ack_waiter_and_emits_canonical_progress(scheduler_instance: ReActScheduler, monkeypatch):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task)
    scheduler_instance._ws_hub = object()

    ack_waiter = asyncio.get_running_loop().create_future()
    observe_waiter = asyncio.get_running_loop().create_future()
    scheduler_instance._bootstrap_ack_waiters["device-1"] = ack_waiter
    scheduler_instance._bootstrap_waiters["device-1"] = observe_waiter
    scheduler_instance._bootstrap_screenshot_msg_ids["device-1"] = "bootstrap-msg-1"

    progress_calls = []
    monkeypatch.setattr(
        scheduler_instance,
        "broadcast_agent_progress",
        lambda **kwargs: progress_calls.append(kwargs),
    )

    handled = await scheduler_instance.handle_bootstrap_ack(
        "device-1",
        "bootstrap-msg-1",
        accepted=True,
    )

    assert handled is True
    assert ack_waiter.done() is True
    assert ack_waiter.result() is True
    assert observe_waiter.done() is False
    assert scheduler_instance._bootstrap_screenshot_msg_ids == {}
    assert [call["stage"] for call in progress_calls] == ["ack_received", "waiting_observe"]
    assert all(call["step_number"] == 0 for call in progress_calls)
    assert all(call["phase"] == "observe" for call in progress_calls)


@pytest.mark.asyncio
async def test_handle_bootstrap_ack_rejection_fails_both_waiters_and_emits_ack_rejected(scheduler_instance: ReActScheduler, monkeypatch):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task)
    scheduler_instance._ws_hub = object()

    ack_waiter = asyncio.get_running_loop().create_future()
    observe_waiter = asyncio.get_running_loop().create_future()
    scheduler_instance._bootstrap_ack_waiters["device-1"] = ack_waiter
    scheduler_instance._bootstrap_waiters["device-1"] = observe_waiter
    scheduler_instance._bootstrap_screenshot_msg_ids["device-1"] = "bootstrap-msg-2"

    progress_calls = []
    monkeypatch.setattr(
        scheduler_instance,
        "broadcast_agent_progress",
        lambda **kwargs: progress_calls.append(kwargs),
    )

    handled = await scheduler_instance.handle_bootstrap_ack(
        "device-1",
        "bootstrap-msg-2",
        accepted=False,
        error="Device not found",
    )

    assert handled is True
    assert ack_waiter.done() is True
    assert observe_waiter.done() is True
    with pytest.raises(ObserveException) as ack_exc:
        ack_waiter.result()
    with pytest.raises(ObserveException) as observe_exc:
        observe_waiter.result()
    assert ack_exc.value.error_type == ReActErrorType.ACK_REJECTED
    assert observe_exc.value.error_type == ReActErrorType.ACK_REJECTED
    assert str(ack_exc.value) == "Device not found"
    assert str(observe_exc.value) == "Device not found"
    assert scheduler_instance._bootstrap_screenshot_msg_ids == {}
    assert len(progress_calls) == 1
    assert progress_calls[0]["stage"] == "ack_rejected"
    assert progress_calls[0]["error_type"] == ReActErrorType.ACK_REJECTED.value
    assert progress_calls[0]["success"] is False


@pytest.mark.asyncio
async def test_request_bootstrap_screenshot_times_out_waiting_for_ack(scheduler_instance: ReActScheduler, monkeypatch):
    task = _make_task("device-1", "task-1")
    task.observe_timeout = 0.01
    _register_task(scheduler_instance, task)
    scheduler_instance._ws_hub = object()

    monkeypatch.setattr(scheduler_instance, "send_to_device", lambda device_id, message: True)

    with pytest.raises(ObserveException) as exc_info:
        await scheduler_instance._request_bootstrap_screenshot(task)

    assert exc_info.value.error_type == ReActErrorType.ACK_TIMEOUT
    assert "ACK timeout" in exc_info.value.message
    assert scheduler_instance._bootstrap_waiters == {}
    assert scheduler_instance._bootstrap_ack_waiters == {}
    assert scheduler_instance._bootstrap_screenshot_msg_ids == {}


@pytest.mark.asyncio
async def test_cleanup_disconnected_device_cancels_bootstrap_ack_waiter(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task)

    ack_waiter = asyncio.get_running_loop().create_future()
    observe_waiter = asyncio.get_running_loop().create_future()
    scheduler_instance._bootstrap_ack_waiters["device-1"] = ack_waiter
    scheduler_instance._bootstrap_waiters["device-1"] = observe_waiter
    scheduler_instance._bootstrap_screenshot_msg_ids["device-1"] = "bootstrap-msg-3"

    await scheduler_instance.cleanup_disconnected_device("device-1")

    assert ack_waiter.cancelled() is True
    assert observe_waiter.cancelled() is True
    assert scheduler_instance._bootstrap_ack_waiters == {}
    assert scheduler_instance._bootstrap_waiters == {}
    assert scheduler_instance._bootstrap_screenshot_msg_ids == {}


@pytest.mark.asyncio
async def test_ensure_bootstrap_observation_maps_observe_error_to_failed_observe_received(scheduler_instance: ReActScheduler, monkeypatch):
    task = _make_task("device-1", "task-1")
    _register_task(scheduler_instance, task)
    scheduler_instance._ws_hub = object()

    async def fake_emit_phase_start(*args, **kwargs):
        return None

    async def fake_guard(*args, **kwargs):
        return True

    async def fake_request_bootstrap(task_obj):
        raise ObserveException(ReActErrorType.OBSERVE_ERROR, "bootstrap failed")

    progress_calls = []
    monkeypatch.setattr(scheduler_instance, "_emit_phase_start", fake_emit_phase_start)
    monkeypatch.setattr(scheduler_instance, "_guard_task_ownership", fake_guard)
    monkeypatch.setattr(scheduler_instance, "_request_bootstrap_screenshot", fake_request_bootstrap)
    monkeypatch.setattr(
        scheduler_instance,
        "broadcast_agent_progress",
        lambda **kwargs: progress_calls.append(kwargs),
    )

    with pytest.raises(ObserveException) as exc_info:
        await scheduler_instance._ensure_bootstrap_observation("device-1", task, execution_token=1)

    assert exc_info.value.error_type == ReActErrorType.OBSERVE_ERROR
    assert [call["stage"] for call in progress_calls] == ["waiting_ack", "observe_received"]
    assert progress_calls[-1]["success"] is False
    assert progress_calls[-1]["error"] == "bootstrap failed"
    assert progress_calls[-1]["error_type"] == ReActErrorType.OBSERVE_ERROR.value
    assert progress_calls[-1]["phase"] == "observe"
    assert progress_calls[-1]["step_number"] == 0


def test_complete_reason_snapshots_before_action_screenshot_and_path():
    task = _make_task("device-1", "task-1")
    task.restore_initial_observe("before-image", "initial observation", screenshot_path="screenshots/bootstrap.png")

    task.complete_reason("reasoning", {"action": "tap"})

    record = task.react_records[-1]
    assert record.before_action_screenshot == "before-image"
    assert record.before_action_screenshot_path == "screenshots/bootstrap.png"


@pytest.mark.asyncio
async def test_set_observe_result_marks_screenshot_unchanged_true_and_stores_path(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    task.restore_initial_observe("", "", screenshot_path="screenshots/bootstrap.png")
    task.initial_screenshot = "data:image/png;base64," + base64.b64encode(b"same-image").decode()
    task.complete_reason("reasoning", {"action": "tap"})
    _register_task(scheduler_instance, task)

    await scheduler_instance.set_observe_result(
        "device-1",
        "data:image/png;base64," + base64.b64encode(b"same-image").decode(),
        "observe ok",
        step_number=1,
        screenshot_path="screenshots/step_1.png",
        success=True,
    )

    record = task.react_records[-1]
    assert record.screenshot_path == "screenshots/step_1.png"
    assert record.screenshot_unchanged is True
    assert record.observation == "observe ok"
    assert record.success is True


@pytest.mark.asyncio
async def test_set_observe_result_marks_screenshot_unchanged_false_for_different_images(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    task.initial_screenshot = base64.b64encode(b"before-image").decode()
    task.complete_reason("reasoning", {"action": "tap"})
    _register_task(scheduler_instance, task)

    await scheduler_instance.set_observe_result(
        "device-1",
        base64.b64encode(b"after-image").decode(),
        "observe changed",
        step_number=1,
        success=True,
    )

    assert task.react_records[-1].screenshot_unchanged is False


@pytest.mark.asyncio
async def test_set_observe_result_step_zero_does_not_compute_unchanged(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    task.initial_screenshot = base64.b64encode(b"bootstrap-before").decode()
    _register_task(scheduler_instance, task)

    await scheduler_instance.set_observe_result(
        "device-1",
        base64.b64encode(b"bootstrap-before").decode(),
        "bootstrap observe",
        step_number=0,
        success=True,
    )

    assert task.initial_screenshot == base64.b64encode(b"bootstrap-before").decode()
    assert task.react_records == []


@pytest.mark.asyncio
async def test_set_observe_result_invalid_or_missing_image_keeps_unchanged_unknown(scheduler_instance: ReActScheduler):
    task = _make_task("device-1", "task-1")
    task.initial_screenshot = "not-valid-base64"
    task.complete_reason("reasoning", {"action": "tap"})
    _register_task(scheduler_instance, task)

    await scheduler_instance.set_observe_result(
        "device-1",
        "",
        "observe missing screenshot",
        step_number=1,
        success=True,
    )
    assert task.react_records[-1].screenshot_unchanged is None

    await scheduler_instance.set_observe_result(
        "device-1",
        "still-not-valid-base64",
        "observe invalid screenshot",
        step_number=1,
        success=True,
    )
    assert task.react_records[-1].screenshot_unchanged is None
