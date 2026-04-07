/**
 * Batch Task Store - Zustand store for batch task management
 *
 * Manages batch task state with WebSocket-driven updates:
 * - batch_step_update: Per-device step updates
 * - batch_device_finished: Per-device completion
 */

import { create } from 'zustand';
import { wsConsoleApi, type WsConsoleMessage } from '../services/wsConsole';
import { agentApi } from '../services/agentApi';
import { batchStoreLogger } from '../hooks/useLogger';

export type BatchTaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'interrupted';

export interface BatchTaskExecution {
  deviceId: string;
  status: BatchTaskStatus;
  currentStep: number;
  maxSteps: number;
  startTime?: string;
  endTime?: string;
  error?: string;
  taskId?: string;
  instruction?: string;
}

interface BatchState {
  // Batch task executions by device ID
  executions: Record<string, BatchTaskExecution>;

  // Overall batch status
  isRunning: boolean;
  totalDevices: number;
  completedCount: number;
  failedCount: number;

  // WebSocket console callback reference
  _wsCallback: ((msg: WsConsoleMessage) => void) | null;

  // Actions
  initBatchSession: (deviceIds: string[]) => void;
  endBatchSession: () => void;

  // Start batch task
  startBatchTask: (params: {
    device_ids: string[];
    instruction: string;
    mode_policy: 'force_cautious' | 'force_normal' | 'default';
    max_steps?: number;
  }) => Promise<void>;

  // Interrupt all
  interruptAll: () => Promise<void>;

  // Internal: handle WebSocket message
  _handleWsMessage: (message: WsConsoleMessage) => void;
  _handleBatchStepUpdate: (message: WsConsoleMessage) => void;
  _handleBatchDeviceFinished: (message: WsConsoleMessage) => void;
  _handleAgentStatus: (message: WsConsoleMessage) => void;

  // Get execution for a device
  getExecution: (deviceId: string) => BatchTaskExecution | undefined;
}

export const useBatchStore = create<BatchState>((set, get) => ({
  executions: {},
  isRunning: false,
  totalDevices: 0,
  completedCount: 0,
  failedCount: 0,
  _wsCallback: null,

  initBatchSession: (deviceIds: string[]) => {
    const state = get();

    // Disconnect previous connection if any
    if (state._wsCallback) {
      wsConsoleApi.removeCallback(state._wsCallback);
    }

    // Connect WebSocket console
    wsConsoleApi.connect();
    wsConsoleApi.addCallback(get()._handleWsMessage);

    // Subscribe to all devices
    deviceIds.forEach((deviceId) => {
      wsConsoleApi.subscribe(deviceId);
    });

    // Initialize executions for all devices
    const executions: Record<string, BatchTaskExecution> = {};
    deviceIds.forEach((deviceId) => {
      executions[deviceId] = {
        deviceId,
        status: 'pending',
        currentStep: 0,
        maxSteps: 100,
      };
    });

    set({
      executions,
      isRunning: false,
      totalDevices: deviceIds.length,
      completedCount: 0,
      failedCount: 0,
    });
  },

  endBatchSession: () => {
    const state = get();

    // Unsubscribe from all devices
    Object.keys(state.executions).forEach((deviceId) => {
      wsConsoleApi.unsubscribe(deviceId);
    });

    // Remove callback
    if (state._wsCallback) {
      wsConsoleApi.removeCallback(state._wsCallback);
    }

    set({
      executions: {},
      isRunning: false,
      totalDevices: 0,
      completedCount: 0,
      failedCount: 0,
      _wsCallback: null,
    });
  },

  startBatchTask: async (params) => {
    const { device_ids, instruction, mode_policy, max_steps } = params;

    batchStoreLogger.info('[startBatchTask] Starting batch task', {
      deviceCount: device_ids.length,
      modePolicy: mode_policy,
    });

    // Initialize executions
    const executions: Record<string, BatchTaskExecution> = {};
    device_ids.forEach((deviceId) => {
      executions[deviceId] = {
        deviceId,
        status: 'running',
        currentStep: 0,
        maxSteps: max_steps || 100,
        startTime: new Date().toISOString(),
        instruction,
      };
    });

    set({
      executions,
      isRunning: true,
      totalDevices: device_ids.length,
      completedCount: 0,
      failedCount: 0,
    });

    try {
      const result = await agentApi.startBatchTasks({
        device_ids,
        instruction,
        mode_policy,
        max_steps,
      });

      batchStoreLogger.info('[startBatchTask] Batch task started', { result });

      // Update task IDs
      result.results.forEach((r) => {
        if (r.task_id && r.status === 'started') {
          set((state) => ({
            executions: {
              ...state.executions,
              [r.device_id]: {
                ...state.executions[r.device_id],
                taskId: r.task_id,
                status: 'running',
              },
            },
          }));
        } else if (r.status === 'error') {
          set((state) => ({
            executions: {
              ...state.executions,
              [r.device_id]: {
                ...state.executions[r.device_id],
                status: 'failed',
                error: r.message || 'Failed to start',
                endTime: new Date().toISOString(),
              },
            },
            failedCount: state.failedCount + 1,
          }));
        }
      });

    } catch (error: any) {
      batchStoreLogger.error('[startBatchTask] Failed to start batch task', {
        error: error.message || 'Unknown error',
      });

      // Mark all as failed
      const executions: Record<string, BatchTaskExecution> = {};
      device_ids.forEach((deviceId) => {
        executions[deviceId] = {
          deviceId,
          status: 'failed',
          currentStep: 0,
          maxSteps: max_steps || 100,
          error: error.message || 'Unknown error',
          endTime: new Date().toISOString(),
        };
      });

      set({
        executions,
        isRunning: false,
        failedCount: device_ids.length,
      });
    }
  },

  interruptAll: async () => {
    const state = get();

    batchStoreLogger.info('[interruptAll] Interrupting all tasks');

    // Send interrupt to all running tasks using backend task IDs
    const runningExecutions = Object.values(state.executions).filter(
      (execution) => execution.status === 'running' && execution.taskId
    );

    await Promise.all(
      runningExecutions.map((execution) =>
        agentApi.interrupt(execution.taskId!).catch((error) => {
          batchStoreLogger.error('[interruptAll] Failed to interrupt device', {
            deviceId: execution.deviceId,
            taskId: execution.taskId,
            error: String(error),
          });
        })
      )
    );

    set((state) => ({
      isRunning: false,
      executions: Object.fromEntries(
        Object.entries(state.executions).map(([deviceId, exec]) => [
          deviceId,
          {
            ...exec,
            status: 'interrupted' as BatchTaskStatus,
            endTime: new Date().toISOString(),
          },
        ])
      ),
    }));
  },

  _handleWsMessage: (message: WsConsoleMessage) => {
    const state = get();

    switch (message.type) {
      case 'batch_step_update':
        if (message.device_id) {
          state._handleBatchStepUpdate(message);
        }
        break;

      case 'batch_device_finished':
        if (message.device_id) {
          state._handleBatchDeviceFinished(message);
        }
        break;

      case 'agent_status':
        // Also handle individual agent_status for batch devices
        if (message.device_id && message.status) {
          state._handleAgentStatus(message);
        }
        break;
    }
  },

  _handleBatchStepUpdate: (message: WsConsoleMessage) => {
    const { device_id, step_number, max_steps } = message;

    set((state) => ({
      executions: {
        ...state.executions,
        [device_id!]: {
          ...state.executions[device_id!],
          currentStep: step_number || state.executions[device_id!].currentStep,
          maxSteps: max_steps || state.executions[device_id!].maxSteps,
          status: 'running',
        },
      },
    }));
  },

  _handleBatchDeviceFinished: (message: WsConsoleMessage) => {
    const { device_id, status, error } = message;

    const finalStatus = status as BatchTaskStatus || (error ? 'failed' : 'completed');

    set((state) => {
      const currentExec = state.executions[device_id!];
      const wasRunning = currentExec.status === 'running';

      return {
        executions: {
          ...state.executions,
          [device_id!]: {
            ...currentExec,
            status: finalStatus,
            endTime: new Date().toISOString(),
            error,
          },
        },
        completedCount: finalStatus === 'completed' && wasRunning
          ? state.completedCount + 1
          : state.completedCount,
        failedCount: finalStatus === 'failed' && wasRunning
          ? state.failedCount + 1
          : state.failedCount,
        isRunning: finalStatus === 'running'
          ? true
          : Object.values({
              ...state.executions,
              [device_id!]: { ...currentExec, status: finalStatus }
            }).some((e) => e.status === 'running'),
      };
    });
  },

  _handleAgentStatus: (message: WsConsoleMessage) => {
    const { device_id, status, message: statusMessage } = message;

    if (!device_id || !status) return;

    const state = get();
    const currentExec = state.executions[device_id];
    if (!currentExec) return;

    let finalStatus: BatchTaskStatus | undefined;
    if (status === 'finished' || status === 'completed') {
      finalStatus = 'completed';
    } else if (status === 'error' || status === 'failed') {
      finalStatus = 'failed';
    } else if (status === 'interrupted') {
      finalStatus = 'interrupted';
    }

    if (finalStatus) {
      set((state) => ({
        executions: {
          ...state.executions,
          [device_id]: {
            ...state.executions[device_id],
            status: finalStatus,
            endTime: new Date().toISOString(),
            error: statusMessage,
          },
        },
        completedCount: finalStatus === 'completed' ? state.completedCount + 1 : state.completedCount,
        failedCount: finalStatus === 'failed' ? state.failedCount + 1 : state.failedCount,
      }));
    }
  },

  getExecution: (deviceId: string) => {
    return get().executions[deviceId];
  },
}));
