import axios from 'axios';
import { wsConsoleApi } from './wsConsole';
import type { ObserveErrorDecisionPayload } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';
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
    status: 'requested' | 'skipped' | 'error';
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
  max_observe_error_retries: number;
  consecutive_observe_error_count: number;
  awaiting_observe_error_decision: boolean;
  pending_observe_error_message: string | null;
  pending_observe_error_prompt: ObserveErrorDecisionPayload | null;
  current_screenshot: string | null;
  current_app: string;
  has_active_task: boolean;
  can_interrupt: boolean;
  can_resume: boolean;
  latest_task: TaskDetailResponse | null;
  chat_history: ChatHistoryMessage[];
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
  task_id?: string;
  step_number?: number;
  phase?: string;
  stage?: string;
  progress_status_text?: string;
  progress_message?: string;
  result?: string;
  success?: boolean;
  error?: string;
  error_type?: string;
  version?: number;
  error_code?: any;
  data?: Record<string, any>;
  observe_error_decision?: ObserveErrorDecisionPayload;
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

function normalizeObserveErrorDecisionPayload(value: any): ObserveErrorDecisionPayload | undefined {
  if (!value || typeof value !== 'object') {
    return undefined;
  }

  if (!value.task_id || !value.device_id || typeof value.message !== 'string') {
    return undefined;
  }

  return {
    task_id: String(value.task_id),
    device_id: String(value.device_id),
    message: value.message,
    consecutive_count: Number(value.consecutive_count ?? 0),
    max_retries: Number(value.max_retries ?? 0),
    step_number: Number(value.step_number ?? 0),
    error_type: typeof value.error_type === 'string' ? value.error_type : undefined,
    stage: typeof value.stage === 'string' ? value.stage : undefined,
    message_id: typeof value.message_id === 'string' ? value.message_id : undefined,
    created_at: typeof value.created_at === 'string' ? value.created_at : undefined,
  };
}

function normalizeChatHistoryMessage(message: ChatHistoryMessage): ChatHistoryMessage {
  const observeErrorDecision = normalizeObserveErrorDecisionPayload(
    message.observe_error_decision ?? (message.stage === 'observe_error_user_decision' ? message.data : undefined),
  );

  return {
    ...message,
    step_number: message.step_number ?? undefined,
    observe_error_decision: observeErrorDecision,
  };
}

function normalizeChatHistoryMessages(messages?: ChatHistoryMessage[]): ChatHistoryMessage[] {
  return (messages || []).map(normalizeChatHistoryMessage);
}

function getSnapshotScreenshot(session: { latest_screenshot?: string | null; chat_history?: ChatHistoryMessage[] }): string | null {
  if (session.latest_screenshot) {
    return session.latest_screenshot;
  }

  for (let i = (session.chat_history || []).length - 1; i >= 0; i -= 1) {
    const screenshot = session.chat_history?.[i]?.screenshot_path;
    if (screenshot) {
      return screenshot;
    }
  }

  return null;
}

function getSnapshotCurrentStep(session: { current_step?: number | null; chat_history?: ChatHistoryMessage[] }): number {
  if (session.current_step != null) {
    return session.current_step;
  }

  for (let i = (session.chat_history || []).length - 1; i >= 0; i -= 1) {
    const stepNumber = session.chat_history?.[i]?.step_number;
    if (stepNumber != null) {
      return stepNumber;
    }
  }

  return 0;
}

function getSessionMode(session: { mode?: string | null }): 'normal' | 'cautious' {
  return normalizeMode(session.mode || undefined);
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

// NOTE: GET /api/v1/tasks/{taskId} does not exist on server.
// Task step details are embedded in DeviceTaskSessionResponse.chat_history.
async function getTaskDetail(_taskId: string): Promise<TaskDetailResponse | null> {
  return null;
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
    max_observe_error_retries?: number;
  }): Promise<{ task_id: string; status: string }> {
    // Send task creation via WebSocket - server will respond with task_created message.
    wsConsoleApi.sendCreateTask(
      deviceId,
      params.instruction,
      params.mode,
      params.max_steps || 100,
      params.max_observe_error_retries ?? 2,
    );

    // The actual task_id will arrive via WebSocket task_created.
    return {
      task_id: '',
      status: 'pending',
    };
  },

  async interruptDevice(deviceId: string): Promise<{ success: boolean }> {
    await api.post(`/api/v1/devices/${deviceId}/interrupt`);
    return { success: true };
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

      const chatHistory = normalizeChatHistoryMessages(session.chat_history);

      return {
        device_id: deviceId,
        task_id: session.task_id || null,
        instruction: session.instruction || null,
        mode: getSessionMode(session),
        status: session.status || 'idle',
        current_step: getSnapshotCurrentStep({ current_step: session.current_step, chat_history: chatHistory }),
        max_steps: session.max_steps || 100,
        max_observe_error_retries: session.max_observe_error_retries ?? 2,
        consecutive_observe_error_count: session.consecutive_observe_error_count ?? 0,
        awaiting_observe_error_decision: Boolean(session.awaiting_observe_error_decision),
        pending_observe_error_message: session.pending_observe_error_message || null,
        pending_observe_error_prompt: session.pending_observe_error_prompt || null,
        current_screenshot: getSnapshotScreenshot({ latest_screenshot: session.latest_screenshot, chat_history: chatHistory }),
        current_app: '未知',
        has_active_task: Boolean(session.interruptible && session.task_id),
        can_interrupt: Boolean(session.interruptible && session.task_id),
        can_resume: false,
        latest_task: null, // Server session endpoint does not provide full task steps
        chat_history: chatHistory,
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
          max_observe_error_retries: 2,
          consecutive_observe_error_count: 0,
          awaiting_observe_error_decision: false,
          pending_observe_error_message: null,
          pending_observe_error_prompt: null,
          current_screenshot: null,
          current_app: '未知',
          has_active_task: false,
          can_interrupt: false,
          can_resume: false,
          latest_task: null,
          chat_history: [],
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
    const mode = params.mode_policy === 'force_cautious'
      ? 'cautious'
      : params.mode_policy === 'force_normal'
        ? 'normal'
        : undefined;

    params.device_ids.forEach((device_id) => {
      wsConsoleApi.sendCreateTask(device_id, params.instruction, mode, params.max_steps || 100);
    });

    const results = params.device_ids.map((device_id) => ({
      device_id,
      status: 'requested' as const,
      task_id: '',
      mode,
      message: 'create_task request sent; waiting for task_created to provide task_id',
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
        messages: normalizeChatHistoryMessages(response.data.messages),
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
