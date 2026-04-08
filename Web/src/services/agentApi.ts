import axios from 'axios';
import { wsConsoleApi } from './wsConsole';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';
const ACTIVE_TASK_STATUSES = new Set(['pending', 'running']);

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json',
  },
});

export interface AgentStepResponse {
  type: 'action' | 'pending' | 'finish' | 'max_steps_reached';
  action: Record<string, any>;
  step_number: number;
  thinking?: string;
  message?: string;
  success?: boolean;
  raw_content?: string;
  status: string;
  screenshot?: string;
  current_app?: string;
}

export interface BatchAgentTaskResponse {
  results: Array<{
    device_id: string;
    status: 'started' | 'skipped' | 'error';
    message?: string;
    session_id?: string;
    task_id?: string;
    mode?: string;
  }>;
}

export interface TaskResponse {
  id: string;
  task_id: string;
  device_id: string;
  instruction: string;
  status: string;
  mode: string;
  max_steps: number;
  current_step: number;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  result?: Record<string, any>;
  error_message?: string | null;
}

export interface TaskStepResponse {
  id: string;
  step_number: number;
  action_type: string;
  action_params: Record<string, any>;
  thinking?: string;
  duration_ms?: number;
  success: boolean;
  error?: string;
  screenshot_url?: string;
  created_at: string;
}

export interface TaskDetailResponse extends TaskResponse {
  steps: TaskStepResponse[];
}

export interface AgentSessionResponse {
  exists: boolean;
  session_id?: string;
  device_id: string;
  platform: string;
  task_id: string;
  instruction: string;
  mode: string;
  status: string;
  current_step: number;
  max_steps: number;
  steps: Array<{
    step_number: number;
    phase: string;
    thinking: string;
    action_type: string;
    action_params: Record<string, any>;
    action_result: string;
    success: boolean;
    timestamp: string;
    screenshot_url?: string;
  }>;
}

export interface DeviceSessionSnapshot {
  device_id: string;
  task_id: string | null;
  instruction: string | null;
  mode: 'normal' | 'cautious';
  status: string;
  current_step: number;
  max_steps: number;
  current_screenshot: string | null;
  current_app: string;
  has_active_task: boolean;
  can_interrupt: boolean;
  can_resume: boolean;
  latest_task: TaskDetailResponse | null;
}

export interface ChatHistoryMessage {
  id: string;
  role: string;
  content: string;
  thinking?: string;
  action_type?: string;
  action_params?: Record<string, any>;
  screenshot_path?: string;
  created_at: string;
}

interface BackendDeviceResponse {
  device_id: string;
  status: string;
  current_task_id?: string | null;
  platform?: string;
}

function normalizeMode(mode?: string): 'normal' | 'cautious' {
  return mode === 'cautious' ? 'cautious' : 'normal';
}

function normalizeStatus(status?: string, fallbackTaskId?: string | null): string {
  if (status) {
    return status;
  }
  return fallbackTaskId ? 'running' : 'idle';
}

function isActiveTaskStatus(status?: string): boolean {
  return ACTIVE_TASK_STATUSES.has(status || 'idle');
}

function getLastScreenshot(task?: TaskDetailResponse | null): string | null {
  if (!task) {
    return null;
  }

  for (let i = task.steps.length - 1; i >= 0; i -= 1) {
    if (task.steps[i].screenshot_url) {
      return task.steps[i].screenshot_url || null;
    }
  }

  const finalScreenshot = task.result?.final_screenshot;
  return typeof finalScreenshot === 'string' ? finalScreenshot : null;
}

function formatTaskStepContent(step: TaskStepResponse): string {
  if (step.error) {
    return `步骤 ${step.step_number} 失败: ${step.error}`;
  }

  const actionType = step.action_type || 'observe';
  const params = step.action_params && Object.keys(step.action_params).length > 0
    ? ` - ${JSON.stringify(step.action_params)}`
    : '';

  return `步骤 ${step.step_number}: ${actionType}${params}`;
}

function buildChatHistoryFromTask(task?: TaskDetailResponse | null): ChatHistoryMessage[] {
  if (!task) {
    return [];
  }

  const messages: ChatHistoryMessage[] = [
    {
      id: `${task.task_id}_user`,
      role: 'user',
      content: task.instruction,
      created_at: task.created_at,
    },
  ];

  task.steps.forEach((step) => {
    messages.push({
      id: step.id,
      role: 'agent',
      content: formatTaskStepContent(step),
      thinking: step.thinking,
      action_type: step.action_type || undefined,
      action_params: step.action_params || {},
      screenshot_path: step.screenshot_url,
      created_at: step.created_at,
    });
  });

  const result = task.result || {};
  const finalMessage =
    task.status === 'completed'
      ? result.finish_message || result.message || '任务已完成'
      : task.status === 'failed'
        ? result.error || result.message || task.error_message || '任务执行失败'
        : task.status === 'interrupted'
          ? result.message || '任务已中断'
          : '';

  if (finalMessage) {
    messages.push({
      id: `${task.task_id}_final_${task.status}`,
      role: 'agent',
      content: finalMessage,
      screenshot_path: getLastScreenshot(task) || undefined,
      created_at: task.finished_at || task.started_at || task.created_at,
    });
  }

  return messages;
}

async function getDeviceById(deviceId: string): Promise<BackendDeviceResponse | null> {
  const response = await api.get('/api/v1/devices');
  const devices = (response.data?.devices || []) as BackendDeviceResponse[];
  return devices.find((device) => device.device_id === deviceId) || null;
}

// NOTE: GET /api/v1/tasks does not exist on server.
// Task info comes from GET /api/v1/devices/{deviceId}/session via getLatestDeviceSnapshot.
async function getLatestTaskForDevice(_deviceId: string): Promise<TaskResponse | null> {
  return null;
}

// NOTE: GET /api/v1/tasks/{taskId} does not exist on server.
// Task step details are embedded in DeviceTaskSessionResponse.chat_history.
async function getTaskDetail(_taskId: string): Promise<TaskDetailResponse | null> {
  return null;
}

// NOTE: Both getTaskDetail and getLatestTaskForDevice now return null
// because the server has no REST endpoints for task list/detail.
// resolveTaskDetail is kept for compatibility but always returns null.
async function resolveTaskDetail(
  _preferredTaskId: string | null,
  _fallbackTaskId: string | null,
): Promise<TaskDetailResponse | null> {
  return null;
}

function buildSnapshotFromTask(
  deviceId: string,
  task: TaskDetailResponse | null,
  preferredTaskId: string | null,
): DeviceSessionSnapshot {
  const taskId = task?.task_id || preferredTaskId || null;
  const status = normalizeStatus(task?.status, taskId);
  const hasActiveTask = !!taskId && isActiveTaskStatus(status);

  return {
    device_id: deviceId,
    task_id: taskId,
    instruction: task?.instruction || null,
    mode: normalizeMode(task?.mode),
    status,
    current_step: task?.current_step || 0,
    max_steps: task?.max_steps || 100,
    current_screenshot: getLastScreenshot(task),
    current_app: typeof task?.result?.current_app === 'string' ? task.result.current_app : '未知',
    has_active_task: hasActiveTask,
    can_interrupt: hasActiveTask && !!taskId,
    can_resume: false,
    latest_task: task,
  };
}

export const agentApi = {
  async createSession(deviceId: string): Promise<{ task_id: string }> {
    const snapshot = await this.getLatestDeviceSnapshot(deviceId);
    return { task_id: snapshot.task_id || '' };
  },

  async getSession(deviceId: string): Promise<AgentSessionResponse> {
    const [device, snapshot] = await Promise.all([
      getDeviceById(deviceId),
      this.getLatestDeviceSnapshot(deviceId),
    ]);
    const task = snapshot.latest_task;

    if (!task) {
      return {
        exists: false,
        device_id: deviceId,
        platform: device?.platform || 'unknown',
        task_id: '',
        instruction: '',
        mode: snapshot.mode,
        status: snapshot.status,
        current_step: 0,
        max_steps: 0,
        steps: [],
      };
    }

    return {
      exists: true,
      session_id: task.task_id,
      device_id: deviceId,
      platform: device?.platform || 'unknown',
      task_id: task.task_id,
      instruction: task.instruction,
      mode: task.mode,
      status: task.status,
      current_step: task.current_step,
      max_steps: task.max_steps,
      steps: task.steps.map((step) => ({
        step_number: step.step_number,
        phase: 'action',
        thinking: step.thinking || '',
        action_type: step.action_type,
        action_params: step.action_params || {},
        action_result: step.error || '',
        success: step.success,
        timestamp: step.created_at,
        screenshot_url: step.screenshot_url,
      })),
    };
  },

  async destroySession(_deviceId: string): Promise<void> {
    return;
  },

  async startTask(deviceId: string, params: {
    task_id: string;
    instruction: string;
    mode?: 'normal' | 'cautious';
    max_steps?: number;
    max_parse_retries?: number;
  }): Promise<{ task_id: string; status: string }> {
    // Send task creation via WebSocket - server will respond with task_created message
    wsConsoleApi.sendCreateTask(deviceId, params.instruction, params.mode || 'normal', params.max_steps || 100);

    // The actual task_id will come back via WebSocket task_created message
    // For now, return a placeholder that will be updated when the message arrives
    // The agentStore handles the task_created message to update currentTaskId
    return {
      task_id: '',  // Will be updated via WebSocket
      status: 'pending',
    };
  },

  async executeStep(_deviceId: string): Promise<AgentStepResponse> {
    throw new Error('Execute step API not available - task execution is driven by WebSocket');
  },

  async executeStepStream(_deviceId: string): Promise<ReadableStream> {
    throw new Error('Streaming API not available - task execution is driven by WebSocket');
  },

  async getAllSessions(): Promise<Record<string, AgentSessionResponse>> {
    const response = await api.get('/api/v1/devices');
    const devices = (response.data?.devices || []) as BackendDeviceResponse[];
    const sessions = await Promise.all(
      devices.map(async (device) => [device.device_id, await this.getSession(device.device_id)] as const),
    );

    return Object.fromEntries(sessions);
  },

  async getLatestDeviceSnapshot(deviceId: string): Promise<DeviceSessionSnapshot> {
    try {
      // Use correct endpoint: /api/v1/devices/{deviceId}/session
      const response = await api.get(`/api/v1/devices/${deviceId}/session`);
      const session = response.data;

      return {
        device_id: deviceId,
        task_id: session.task_id || null,
        instruction: session.instruction || null,
        mode: normalizeMode(),
        status: session.status || 'idle',
        current_step: session.current_step || 0,
        max_steps: session.max_steps || 100,
        current_screenshot: session.latest_screenshot || null,
        current_app: '未知',
        has_active_task: Boolean(session.interruptible && session.task_id),
        can_interrupt: Boolean(session.interruptible && session.task_id),
        can_resume: false,
        latest_task: null, // Server session endpoint does not provide full task steps
      };
    } catch (error: any) {
      if (error?.response?.status === 404) {
        // Device not found — return empty snapshot
        return {
          device_id: deviceId,
          task_id: null,
          instruction: null,
          mode: 'normal',
          status: 'offline',
          current_step: 0,
          max_steps: 100,
          current_screenshot: null,
          current_app: '未知',
          has_active_task: false,
          can_interrupt: false,
          can_resume: false,
          latest_task: null,
        };
      }
      throw error;
    }
  },

  async startBatchTasks(params: {
    device_ids: string[];
    instruction: string;
    mode_policy: 'force_cautious' | 'force_normal' | 'default';
    max_steps?: number;
  }): Promise<BatchAgentTaskResponse> {
    const mode = params.mode_policy === 'force_cautious' ? 'cautious' : 'normal';

    // Send task creation via WebSocket for each device
    params.device_ids.forEach((device_id) => {
      wsConsoleApi.sendCreateTask(device_id, params.instruction, mode, params.max_steps || 100);
    });

    // Return pending status - actual results come via WebSocket messages
    const results = params.device_ids.map((device_id) => ({
      device_id,
      status: 'started' as const,
      task_id: '',  // Will be updated via WebSocket
      mode,
    }));

    return { results };
  },

  async getChatHistory(deviceId: string, limit: number = 20, taskId?: string): Promise<{
    device_id: string;
    messages: ChatHistoryMessage[];
    total: number;
  }> {
    try {
      const response = await api.get(`/api/v1/devices/${deviceId}/chat`, {
        params: {
          limit,
        },
      });

      return {
        device_id: response.data.device_id,
        messages: response.data.messages || [],
        total: response.data.total || 0,
      };
    } catch (error: any) {
      if (error?.response?.status !== 404) {
        throw error;
      }

      const task = taskId
        ? await getTaskDetail(taskId)
        : (await this.getLatestDeviceSnapshot(deviceId)).latest_task;

      const fullHistory = buildChatHistoryFromTask(task);

      return {
        device_id: deviceId,
        messages: fullHistory.slice(-Math.max(limit, 1)),
        total: fullHistory.length,
      };
    }
  },

  async addChatMessage(deviceId: string, message: {
    role: string;
    content: string;
    thinking?: string;
    action_type?: string;
    action_params?: Record<string, any>;
    screenshot_path?: string;
  }): Promise<{ id: string; created_at: string }> {
    const response = await api.post(`/api/v1/devices/${deviceId}/chat`, message);
    return response.data?.data || { id: `msg_${Date.now()}`, created_at: new Date().toISOString() };
  },

  async clearChatHistory(deviceId: string): Promise<{ success: boolean }> {
    await api.delete(`/api/v1/devices/${deviceId}/chat`);
    return { success: true };
  },

  async uploadScreenshot(_deviceId: string, _screenshot: string): Promise<{
    success: boolean;
    message: string;
  }> {
    return { success: false, message: '截图通过 WebSocket 实时推送，暂不支持手动上传' };
  },
};

export default agentApi;
