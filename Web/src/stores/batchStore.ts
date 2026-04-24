/**
 * Batch Task Store - Zustand store for batch task management
 *
 * Primary contract:
 * - create_task is sent per device over wsConsoleApi
 * - task_created binds the real task_id to each device execution
 * - agent_status advances running/completed/failed/interrupted state
 *
 * Legacy compatibility:
 * - batch_step_update and batch_device_finished are still consumed if present,
 *   but they are no longer required for the main flow.
 */

import { create } from 'zustand';
import { wsConsoleApi, type WsConsoleMessage } from '../services/wsConsole';
import { agentApi } from '../services/agentApi';
import { batchStoreLogger } from '../hooks/useLogger';

export type BatchTaskStatus =
  | 'pending'
  | 'starting'
  | 'running'
  | 'completed'
  | 'failed'
  | 'interrupted';

export interface BatchTaskExecution {
  deviceId: string;
  status: BatchTaskStatus;
  currentStep: number;
  maxSteps: number;
  startTime?: string;
  endTime?: string;
  error?: string;
  taskId?: string;  // deprecated alias for sessionId
  sessionId?: string;  // 持久会话ID
  runId?: string;  // 每次自动运行ID
  instruction?: string;
  statusMessage?: string;
}

interface BatchState {
  executions: Record<string, BatchTaskExecution>;
  candidateDeviceIds: string[];
  instruction: string;
  maxSteps: number;
  modePolicy: 'force_cautious' | 'force_normal' | 'default';
  stopOnError: boolean;

  isRunning: boolean;
  totalDevices: number;
  completedCount: number;
  failedCount: number;

  _wsCallback: ((msg: WsConsoleMessage) => void) | null;
  _stopOnErrorTriggered: boolean;

  initBatchSession: (deviceIds: string[]) => void;
  endBatchSession: () => void;
  startBatchTask: (params: {
    device_ids: string[];
    instruction: string;
    mode_policy: 'force_cautious' | 'force_normal' | 'default';
    max_steps?: number;
    stop_on_error?: boolean;
  }) => Promise<void>;
  interruptAll: () => Promise<void>;

  _handleWsMessage: (message: WsConsoleMessage) => void;
  _handleTaskCreated: (message: WsConsoleMessage) => void;
  _handleBatchStepUpdate: (message: WsConsoleMessage) => void;
  _handleBatchDeviceFinished: (message: WsConsoleMessage) => void;
  _handleAgentStatus: (message: WsConsoleMessage) => void;

  getExecution: (deviceId: string) => BatchTaskExecution | undefined;
}

const TERMINAL_STATUSES: BatchTaskStatus[] = ['completed', 'failed', 'interrupted'];
const ACTIVE_STATUSES: BatchTaskStatus[] = ['starting', 'running'];

function isTerminalStatus(status: BatchTaskStatus | undefined): boolean {
  return !!status && TERMINAL_STATUSES.includes(status);
}

function isActiveStatus(status: BatchTaskStatus | undefined): boolean {
  return !!status && ACTIVE_STATUSES.includes(status);
}

function computeCompletedCount(executions: Record<string, BatchTaskExecution>): number {
  return Object.values(executions).filter((execution) => execution.status === 'completed').length;
}

function computeFailedCount(executions: Record<string, BatchTaskExecution>): number {
  return Object.values(executions).filter((execution) => execution.status === 'failed').length;
}

function computeIsRunning(executions: Record<string, BatchTaskExecution>): boolean {
  return Object.values(executions).some((execution) => isActiveStatus(execution.status));
}

function applyExecutionPatch(
  executions: Record<string, BatchTaskExecution>,
  deviceId: string,
  patch: Partial<BatchTaskExecution>
): Record<string, BatchTaskExecution> {
  const current = executions[deviceId];
  if (!current) {
    return executions;
  }

  return {
    ...executions,
    [deviceId]: {
      ...current,
      ...patch,
    },
  };
}

function updateExecutionState(
  state: Pick<BatchState, 'executions' | 'candidateDeviceIds' | '_stopOnErrorTriggered' | 'stopOnError'>,
  deviceId: string,
  patch: Partial<BatchTaskExecution>
): Pick<BatchState, 'executions' | 'completedCount' | 'failedCount' | 'isRunning' | '_stopOnErrorTriggered'> {
  const nextExecutions = applyExecutionPatch(state.executions, deviceId, patch);
  const failedCount = computeFailedCount(nextExecutions);
  const shouldTriggerStopOnError =
    state.stopOnError &&
    !state._stopOnErrorTriggered &&
    failedCount > computeFailedCount(state.executions) &&
    Object.values(nextExecutions).some((execution) => isActiveStatus(execution.status));

  return {
    executions: nextExecutions,
    completedCount: computeCompletedCount(nextExecutions),
    failedCount,
    isRunning: computeIsRunning(nextExecutions),
    _stopOnErrorTriggered: state._stopOnErrorTriggered || shouldTriggerStopOnError,
  };
}

export const useBatchStore = create<BatchState>((set, get) => ({
  executions: {},
  candidateDeviceIds: [],
  instruction: '',
  maxSteps: 100,
  modePolicy: 'default',
  stopOnError: false,

  isRunning: false,
  totalDevices: 0,
  completedCount: 0,
  failedCount: 0,

  _wsCallback: null,
  _stopOnErrorTriggered: false,

  initBatchSession: (deviceIds: string[]) => {
    const state = get();

    if (state._wsCallback) {
      wsConsoleApi.removeCallback(state._wsCallback);
    }

    wsConsoleApi.connect();
    const callback = get()._handleWsMessage;
    wsConsoleApi.addCallback(callback);

    deviceIds.forEach((deviceId) => {
      wsConsoleApi.subscribe(deviceId);
    });

    set({
      executions: {},
      candidateDeviceIds: [...deviceIds],
      instruction: '',
      maxSteps: 100,
      modePolicy: 'default',
      stopOnError: false,
      isRunning: false,
      totalDevices: deviceIds.length,
      completedCount: 0,
      failedCount: 0,
      _wsCallback: callback,
      _stopOnErrorTriggered: false,
    });
  },

  endBatchSession: () => {
    const state = get();

    state.candidateDeviceIds.forEach((deviceId) => {
      wsConsoleApi.unsubscribe(deviceId);
    });

    if (state._wsCallback) {
      wsConsoleApi.removeCallback(state._wsCallback);
    }

    set({
      executions: {},
      candidateDeviceIds: [],
      instruction: '',
      maxSteps: 100,
      modePolicy: 'default',
      stopOnError: false,
      isRunning: false,
      totalDevices: 0,
      completedCount: 0,
      failedCount: 0,
      _wsCallback: null,
      _stopOnErrorTriggered: false,
    });
  },

  startBatchTask: async (params) => {
    const { device_ids, instruction, mode_policy, max_steps, stop_on_error } = params;
    const effectiveMaxSteps = max_steps || 100;
    const startTime = new Date().toISOString();

    batchStoreLogger.info('[startBatchTask] Starting batch task', {
      deviceCount: device_ids.length,
      modePolicy: mode_policy,
      stopOnError: !!stop_on_error,
    });

    const executions: Record<string, BatchTaskExecution> = {};
    device_ids.forEach((deviceId) => {
      executions[deviceId] = {
        deviceId,
        status: 'starting',
        currentStep: 0,
        maxSteps: effectiveMaxSteps,
        startTime,
        instruction,
        statusMessage: '已发送 create_task，等待 task_created',
      };
    });

    set({
      executions,
      candidateDeviceIds: [...device_ids],
      instruction,
      maxSteps: effectiveMaxSteps,
      modePolicy: mode_policy,
      stopOnError: !!stop_on_error,
      isRunning: device_ids.length > 0,
      totalDevices: device_ids.length,
      completedCount: 0,
      failedCount: 0,
      _stopOnErrorTriggered: false,
    });

    try {
      const result = await agentApi.startBatchTasks({
        device_ids,
        instruction,
        mode_policy,
        max_steps,
      });

      batchStoreLogger.info('[startBatchTask] Batch task requests submitted', { result });

      result.results.forEach((entry) => {
        if (entry.status === 'error') {
          set((state) => ({
            ...updateExecutionState(state, entry.device_id, {
              status: 'failed',
              error: entry.message || 'Failed to start',
              statusMessage: entry.message || '启动请求失败',
              endTime: new Date().toISOString(),
            }),
          }));
          return;
        }

        set((state) => ({
          ...updateExecutionState(state, entry.device_id, {
            status: 'starting',
            statusMessage: entry.message || '等待 task_created',
          }),
        }));
      });
    } catch (error: any) {
      batchStoreLogger.error('[startBatchTask] Failed to start batch task', {
        error: error.message || 'Unknown error',
      });

      const endTime = new Date().toISOString();
      const failedExecutions: Record<string, BatchTaskExecution> = {};
      device_ids.forEach((deviceId) => {
        failedExecutions[deviceId] = {
          deviceId,
          status: 'failed',
          currentStep: 0,
          maxSteps: effectiveMaxSteps,
          instruction,
          startTime,
          endTime,
          error: error.message || 'Unknown error',
          statusMessage: error.message || 'Unknown error',
        };
      });

      set({
        executions: failedExecutions,
        isRunning: false,
        completedCount: 0,
        failedCount: device_ids.length,
      });
    }
  },

  interruptAll: async () => {
    const state = get();
    const runningExecutions = Object.values(state.executions).filter((execution) =>
      isActiveStatus(execution.status)
    );

    batchStoreLogger.info('[interruptAll] Interrupting active batch devices', {
      count: runningExecutions.length,
      deviceIds: runningExecutions.map((execution) => execution.deviceId),
    });

    await Promise.all(
      runningExecutions.map((execution) =>
        agentApi.interruptDevice(execution.deviceId).catch((error: unknown) => {
          batchStoreLogger.error('[interruptAll] Failed to interrupt device', {
            deviceId: execution.deviceId,
            taskId: execution.taskId,
            error: String(error),
          });
        })
      )
    );

    set((currentState) => {
      let nextExecutions = currentState.executions;
      const endTime = new Date().toISOString();

      runningExecutions.forEach((execution) => {
        nextExecutions = applyExecutionPatch(nextExecutions, execution.deviceId, {
          status: 'interrupted',
          endTime,
          statusMessage: '已发送中断请求',
        });
      });

      return {
        executions: nextExecutions,
        completedCount: computeCompletedCount(nextExecutions),
        failedCount: computeFailedCount(nextExecutions),
        isRunning: computeIsRunning(nextExecutions),
      };
    });
  },

  _handleWsMessage: (message: WsConsoleMessage) => {
    switch (message.type) {
      case 'task_created':
        if (message.device_id) {
          get()._handleTaskCreated(message);
        }
        break;

      case 'batch_step_update':
        if (message.device_id) {
          get()._handleBatchStepUpdate(message);
        }
        break;

      case 'batch_device_finished':
        if (message.device_id) {
          get()._handleBatchDeviceFinished(message);
        }
        break;

      case 'agent_status':
        if (message.device_id && message.status) {
          get()._handleAgentStatus(message);
        }
        break;
    }
  },

  _handleTaskCreated: (message: WsConsoleMessage) => {
    const { device_id, task_id, session_id, run_id } = message;
    if (!device_id) {
      return;
    }

    const state = get();
    if (!state.executions[device_id]) {
      return;
    }

    batchStoreLogger.debug(
      `[batch] task_created: device=${device_id}, task_id=${task_id}, session=${session_id}, run=${run_id}`
    );

    set((currentState) => ({
      ...updateExecutionState(currentState, device_id, {
        taskId: task_id,
        sessionId: session_id || task_id,
        runId: run_id,
        status: 'running',
        currentStep: 0,
        statusMessage: '已收到 task_created，运行开始',
      }),
    }));
  },

  _handleBatchStepUpdate: (message: WsConsoleMessage) => {
    const { device_id, step_number, max_steps } = message;
    if (!device_id) {
      return;
    }

    const state = get();
    if (!state.executions[device_id]) {
      return;
    }

    set((currentState) => ({
      ...updateExecutionState(currentState, device_id, {
        status: 'running',
        currentStep: step_number ?? currentState.executions[device_id].currentStep,
        maxSteps: max_steps ?? currentState.executions[device_id].maxSteps,
        statusMessage: '收到批处理兼容进度更新',
      }),
    }));
  },

  _handleBatchDeviceFinished: (message: WsConsoleMessage) => {
    const { device_id, status, error, step_number, max_steps, task_id } = message;
    if (!device_id) {
      return;
    }

    const state = get();
    if (!state.executions[device_id]) {
      return;
    }

    const mappedStatus: BatchTaskStatus =
      status === 'completed'
        ? 'completed'
        : status === 'interrupted'
          ? 'interrupted'
          : 'failed';

    set((currentState) => ({
      ...updateExecutionState(currentState, device_id, {
        taskId: task_id || currentState.executions[device_id].taskId,
        status: mappedStatus,
        currentStep: step_number ?? currentState.executions[device_id].currentStep,
        maxSteps: max_steps ?? currentState.executions[device_id].maxSteps,
        error,
        endTime: new Date().toISOString(),
        statusMessage: error || '收到批处理兼容终态消息',
      }),
    }));

    const latestState = get();
    if (mappedStatus === 'failed' && latestState.stopOnError && latestState._stopOnErrorTriggered) {
      void latestState.interruptAll();
    }
  },

  _handleAgentStatus: (message: WsConsoleMessage) => {
    const { device_id, status, message: statusMessage, step_number, max_steps, task_id } = message;
    const data = message.data || {};
    const session_id = message.session_id || data.session_id;
    const run_id = message.run_id || data.run_id;
    if (!device_id || !status) {
      return;
    }

    const state = get();
    if (!state.executions[device_id]) {
      return;
    }

    let mappedStatus: BatchTaskStatus | null = null;
    if (status === 'pending') {
      mappedStatus = (state.executions[device_id].sessionId || state.executions[device_id].runId)
        ? 'running'
        : 'starting';
    } else if (status === 'running') {
      mappedStatus = 'running';
    } else if (status === 'finished' || status === 'completed') {
      mappedStatus = 'completed';
    } else if (status === 'error' || status === 'failed') {
      mappedStatus = 'failed';
    } else if (status === 'interrupted') {
      mappedStatus = 'interrupted';
    }

    if (!mappedStatus) {
      return;
    }

    const currentExecution = state.executions[device_id];
    const patch: Partial<BatchTaskExecution> = {
      taskId: task_id || currentExecution.taskId,
      sessionId: session_id || currentExecution.sessionId,
      runId: run_id || currentExecution.runId,
      status: mappedStatus,
      statusMessage,
      error: mappedStatus === 'failed' ? statusMessage : currentExecution.error,
      currentStep: step_number ?? currentExecution.currentStep,
      maxSteps: max_steps ?? currentExecution.maxSteps,
    };

    if (isTerminalStatus(mappedStatus) && !currentExecution.endTime) {
      patch.endTime = new Date().toISOString();
    }

    set((currentState) => ({
      ...updateExecutionState(currentState, device_id, patch),
    }));

    const latestState = get();
    if (mappedStatus === 'failed' && latestState.stopOnError && latestState._stopOnErrorTriggered) {
      void latestState.interruptAll();
    }
  },

  getExecution: (deviceId: string) => get().executions[deviceId],
}));
