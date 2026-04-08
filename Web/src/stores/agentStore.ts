import { create } from 'zustand';
import type { AgentStep, AgentMode, PendingAction, TaskStatus, ChatMessage, AgentAction } from '../types';
import { agentApi } from '../services/agentApi';
import { wsConsoleApi, type WsConsoleMessage } from '../services/wsConsole';
import { useDeviceStore } from './deviceStore';
import { agentStoreLogger } from '../hooks/useLogger';

const MAX_MEMORY_ROUNDS = 10; // 最大记忆轮数

// 从 API 加载聊天历史
async function loadConversationHistoryFromAPI(deviceId: string, taskId?: string | null): Promise<ChatMessage[]> {
  try {
    const response = await agentApi.getChatHistory(deviceId, 50, taskId || undefined);
    return response.messages.map((msg) => ({
      id: msg.id,
      role: msg.role as 'user' | 'agent',
      content: msg.content,
      timestamp: msg.created_at,
      thinking: msg.thinking,
      action: msg.action_type ? {
        type: msg.action_type as any,
        params: msg.action_params || {},
        description: msg.action_type,
      } : undefined,
      screenshot: msg.screenshot_path,
    }));
  } catch (e) {
    console.error('Failed to load conversation history from API:', e);
    return [];
  }
}

function normalizeStoreStatus(status?: string): TaskStatus {
  if (status === 'completed' || status === 'failed' || status === 'interrupted' || status === 'running') {
    return status;
  }
  return 'pending';
}

function hasActiveBackendTask(taskId?: string | null, status?: string, canInterrupt?: boolean): boolean {
  return Boolean(taskId) && (Boolean(canInterrupt) || status === 'pending' || status === 'running');
}

function buildHistoryFromSnapshot(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>): AgentStep[] {
  return (snapshot.latest_task?.steps || []).map((step) => ({
    id: step.id,
    phase: 'action',
    action: {
      type: (step.action_type || 'unknown') as any,
      params: step.action_params || {},
      description: formatActionDescription({
        action: step.action_type,
        ...(step.action_params || {}),
      }),
    },
    thinking: step.thinking || '',
    timestamp: step.created_at,
    success: step.success,
    error: step.error,
    step_number: step.step_number,
  }));
}

function getCurrentStepFromHistory(history: AgentStep[]): AgentStep | null {
  return history.length > 0 ? history[history.length - 1] : null;
}

// 保存单条消息到 API
async function saveMessageToAPI(
  deviceId: string,
  role: 'user' | 'agent',
  content: string,
  thinking?: string,
  action?: AgentAction,
  screenshotPath?: string
): Promise<void> {
  try {
    await agentApi.addChatMessage(deviceId, {
      role,
      content,
      thinking,
      action_type: action?.type,
      action_params: action?.params,
      screenshot_path: screenshotPath,
    });
  } catch (e) {
    console.error('Failed to save message to API:', e);
  }
}

interface AgentState {
  // Current session
  currentDeviceId: string | null;
  currentTaskId: string | null;
  currentInstruction: string | null; // 当前任务指令
  mode: AgentMode;
  isRunning: boolean;

  // Main control session
  isLocked: boolean;  // Whether this console has main control
  controllerId: string | null;  // ID of the console that has main control

  // Steps
  currentStep: AgentStep | null;
  history: AgentStep[];
  pendingAction: PendingAction | null;

  // Progress
  status: TaskStatus;
  currentStepNum: number;
  maxSteps: number;
  maxParseRetries: number; // 动作解析失败最大重试次数

  // Error handling
  error: string | null;

  // 对话历史（气泡式对话）
  conversationHistory: ChatMessage[];

  // 实时截图
  currentScreenshot: string | null;
  currentApp: string;

  // Phase tracking (for real-time phase display)
  currentPhase: 'reason' | 'act' | 'observe' | null;
  thinkingContent: string;
  isThinking: boolean;

  // Confirmation state (cautious mode)
  waitingForConfirm: boolean;
  waitingConfirmPhase: 'reason' | 'act' | 'observe' | null;

  // WebSocket console callback reference
  _wsCallback: ((msg: WsConsoleMessage) => void) | null;

  // Derived backend-truth flags
  canInterrupt: boolean;
  canResume: boolean;

  // Actions
  initSession: (deviceId: string, mode?: AgentMode) => void;
  endSession: () => void;
  setMode: (mode: AgentMode) => void;
  setMaxParseRetries: (retries: number) => void;

  // Command - starts task (WS-driven, no polling)
  sendCommand: (command: string) => Promise<void>;

  // Step management
  appendStep: (step: AgentStep) => void;
  setCurrentStep: (step: AgentStep | null) => void;
  setPendingAction: (action: PendingAction | null) => void;

  // Cautious mode actions
  confirmAction: () => Promise<void>;
  rejectAction: () => Promise<void>;
  skipAction: () => Promise<void>;

  // Phase confirmation (cautious mode)
  confirmPhase: (approved: boolean) => void;

  // Task control
  interrupt: () => Promise<void>;
  resume: () => Promise<void>;
  continueTask: (additionalSteps?: number) => Promise<void>;

  // 对话历史管理
  addUserMessage: (content: string) => void;
  addAgentMessage: (content: string, thinking?: string, action?: AgentAction, screenshot?: string, rawContent?: string, isParseError?: boolean) => void;
  updateAgentMessage: (messageId: string, updates: Partial<ChatMessage>) => void;
  forgetOldMessages: (keepCount?: number) => void;
  clearConversation: () => void;

  // 截图管理
  setScreenshot: (screenshot: string | null) => void;
  setCurrentApp: (app: string) => void;

  // Internal: handle WebSocket message
  _handleWsMessage: (message: WsConsoleMessage) => void;
  _handleAgentStep: (message: WsConsoleMessage) => void;
  _handleAgentStatus: (message: WsConsoleMessage) => void;
  _handleActionPending: (message: WsConsoleMessage) => void;
  _handleSessionLocked: (message: WsConsoleMessage) => void;
  _handleSessionReleased: (message: WsConsoleMessage) => void;
  _handlePhaseStart: (message: WsConsoleMessage) => void;
  _handlePhaseEnd: (message: WsConsoleMessage) => void;
  _handleThinking: (message: WsConsoleMessage) => void;
  createStreamingAgentMessage: () => string;
}

// 生成唯一Id
const generateId = () => `msg_${Date.now()}_${Math.random().toString(36).substr(2, 8)}`;

export const useAgentStore = create<AgentState>((set, get) => ({
  currentDeviceId: null,
  currentTaskId: null,
  currentInstruction: null,
  mode: 'normal',
  isRunning: false,
  isLocked: false,
  controllerId: null,
  currentStep: null,
  history: [],
  pendingAction: null,
  status: 'pending',
  currentStepNum: 0,
  maxSteps: 100,
  maxParseRetries: 2,
  error: null,
  conversationHistory: [],
  currentScreenshot: null,
  currentApp: '未知',
  currentPhase: null,
  thinkingContent: '',
  isThinking: false,
  waitingForConfirm: false,
  waitingConfirmPhase: null,
  _wsCallback: null,
  canInterrupt: false,
  canResume: false,

  initSession: async (deviceId, mode = 'normal') => {
    const wsCallback = useAgentStore.getState()._handleWsMessage;

    if (wsCallback) {
      wsConsoleApi.removeCallback(wsCallback);
    }

    wsConsoleApi.connect();
    wsConsoleApi.subscribe(deviceId);
    wsConsoleApi.addCallback(useAgentStore.getState()._handleWsMessage);

    try {
      const snapshot = await agentApi.getLatestDeviceSnapshot(deviceId);
      const savedHistory = await loadConversationHistoryFromAPI(deviceId, snapshot.task_id);
      const history = buildHistoryFromSnapshot(snapshot);
      const currentStep = getCurrentStepFromHistory(history);
      const normalizedStatus = normalizeStoreStatus(snapshot.status);
      const isRunning = hasActiveBackendTask(snapshot.task_id, snapshot.status, snapshot.can_interrupt);

      set({
        currentDeviceId: deviceId,
        currentTaskId: snapshot.task_id,
        currentInstruction: snapshot.instruction,
        mode: snapshot.task_id ? snapshot.mode : mode,
        isRunning,
        isLocked: true,
        controllerId: wsConsoleApi.getConsoleId(),
        currentStep,
        history,
        pendingAction: null,
        status: normalizedStatus,
        currentStepNum: snapshot.current_step,
        maxSteps: snapshot.max_steps,
        error: null,
        conversationHistory: savedHistory,
        currentScreenshot: snapshot.current_screenshot,
        currentApp: snapshot.current_app || '未知',
        currentPhase: null,
        thinkingContent: '',
        isThinking: false,
        waitingForConfirm: false,
        waitingConfirmPhase: null,
        canInterrupt: snapshot.can_interrupt,
        canResume: snapshot.can_resume,
      });
    } catch (error: any) {
      console.error('Failed to initialize agent session:', error);
      set({
        currentDeviceId: deviceId,
        currentTaskId: null,
        currentInstruction: null,
        mode,
        isRunning: false,
        isLocked: true,
        controllerId: wsConsoleApi.getConsoleId(),
        currentStep: null,
        history: [],
        pendingAction: null,
        status: 'pending',
        currentStepNum: 0,
        maxSteps: 100,
        error: error.message || 'Failed to initialize session',
        conversationHistory: [],
        currentScreenshot: null,
        currentApp: '未知',
        currentPhase: null,
        thinkingContent: '',
        isThinking: false,
        waitingForConfirm: false,
        waitingConfirmPhase: null,
        canInterrupt: false,
        canResume: false,
      });
    }
  },

  endSession: () => {
    const state = get();

    // Unsubscribe and disconnect WebSocket
    if (state.currentDeviceId) {
      wsConsoleApi.unsubscribe(state.currentDeviceId);
    }

    // Remove callback using the same reference we added
    const wsCallback = useAgentStore.getState()._handleWsMessage;
    if (wsCallback) {
      wsConsoleApi.removeCallback(wsCallback);
    }

    set({
      currentDeviceId: null,
      currentTaskId: null,
      currentInstruction: null,
      isRunning: false,
      isLocked: false,
      controllerId: null,
      currentStep: null,
      pendingAction: null,
      status: 'pending',
      history: [],
      conversationHistory: [],
      currentScreenshot: null,
      currentApp: '未知',
      currentPhase: null,
      thinkingContent: '',
      isThinking: false,
      waitingForConfirm: false,
      waitingConfirmPhase: null,
      canInterrupt: false,
      canResume: false,
    });
  },

  setMode: (mode) => {
    set({ mode });
  },

  setMaxParseRetries: (retries) => {
    set({ maxParseRetries: retries });
  },

  // WebSocket message handler
  _handleWsMessage: (message: WsConsoleMessage) => {
    const state = get();
    const deviceId = state.currentDeviceId;

    if (!deviceId) return;

    switch (message.type) {
      case 'agent_step':
        if (message.device_id === deviceId) {
          state._handleAgentStep(message);
        }
        break;

      case 'agent_status':
        if (message.device_id === deviceId) {
          state._handleAgentStatus(message);
        }
        break;

      case 'agent_action_pending':
        if (message.device_id === deviceId) {
          state._handleActionPending(message);
        }
        break;

      case 'agent_phase_start':
        if (message.device_id === deviceId) {
          state._handlePhaseStart(message);
        }
        break;

      case 'agent_phase_end':
        if (message.device_id === deviceId) {
          state._handlePhaseEnd(message);
        }
        break;

      case 'agent_thinking':
        if (message.device_id === deviceId) {
          state._handleThinking(message);
        }
        break;

      case 'phase_confirmed':
        // Phase confirmation response from server
        agentStoreLogger.info('[handlePhaseConfirmed] Phase confirmed');
        break;

      case 'session_locked':
        if (message.device_id === deviceId) {
          state._handleSessionLocked(message);
        }
        break;

      case 'session_released':
        if (message.device_id === deviceId) {
          state._handleSessionReleased(message);
        }
        break;

      case 'task_created':
        if (message.device_id === deviceId) {
          agentStoreLogger.info('[handleTaskCreated] Task created via WebSocket', {
            taskId: message.task_id,
            deviceId: message.device_id,
          });
          set({
            currentTaskId: message.task_id,
            isRunning: true,
            status: 'running',
            currentStepNum: 0,
            history: [],
            currentStep: null,
            currentScreenshot: null,
            currentApp: '未知',
            canInterrupt: true,
            canResume: false,
          });
        }
        break;
    }
  },

  // 添加用户消息到对话历史
  addUserMessage: (content: string) => {
    const message: ChatMessage = {
      id: generateId(),
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
    };
    const state = get();
    set({ conversationHistory: [...state.conversationHistory, message] });

    // 保存到 API
    if (state.currentDeviceId) {
      saveMessageToAPI(state.currentDeviceId, 'user', content);
    }
  },

  // 添加Agent消息到对话历史
  addAgentMessage: (content: string, thinking?: string, action?: AgentAction, screenshot?: string, rawContent?: string, isParseError?: boolean) => {
    const message: ChatMessage = {
      id: generateId(),
      role: 'agent',
      content,
      timestamp: new Date().toISOString(),
      thinking,
      action,
      screenshot,
      rawContent,
      isParseError,
    };
    const state = get();
    set({ conversationHistory: [...state.conversationHistory, message] });

    // 保存到 API (截图先保存到本地，之后可以上传)
    if (state.currentDeviceId) {
      saveMessageToAPI(state.currentDeviceId, 'agent', content, thinking, action, screenshot);
    }
  },

  // 更新Agent消息（用于流式更新）
  updateAgentMessage: (messageId: string, updates: Partial<ChatMessage>) => {
    set((state) => ({
      conversationHistory: state.conversationHistory.map((msg) =>
        msg.id === messageId ? { ...msg, ...updates } : msg
      ),
    }));
  },

  // 创建流式Agent消息并返回ID
  createStreamingAgentMessage: () => {
    const message: ChatMessage = {
      id: generateId(),
      role: 'agent',
      content: '',
      timestamp: new Date().toISOString(),
      thinking: '',
    };
    const state = get();
    set({ conversationHistory: [...state.conversationHistory, message] });
    return message.id;
  },

  // 遗忘机制 - 保留最近的N轮对话
  forgetOldMessages: (keepCount = MAX_MEMORY_ROUNDS) => {
    set((state) => {
      const totalMessages = state.conversationHistory.length;
      if (totalMessages <= keepCount * 2) {
        // 不足10轮，无需遗忘
        return state;
      }
      // 保留最近的N轮对话（每轮包含用户消息和Agent回复）
      const messagesToKeep = keepCount * 2;
      const forgottenMessages = state.conversationHistory.slice(0, totalMessages - messagesToKeep);
      console.log(`遗忘机制触发: 遗忘${forgottenMessages.length}条旧消息，保留${messagesToKeep}条新消息`);
      const newHistory = state.conversationHistory.slice(totalMessages - messagesToKeep);
      // 服务器端会处理 retention，前端只需更新本地状态
      return {
        conversationHistory: newHistory,
      };
    });
  },

  // 清空对话历史
  clearConversation: async () => {
    const state = get();
    if (state.currentDeviceId) {
      try {
        await agentApi.clearChatHistory(state.currentDeviceId);
      } catch (e) {
        console.error('Failed to clear chat history on server:', e);
      }
    }
    set({ conversationHistory: [] });
  },

  // 设置截图
  setScreenshot: (screenshot) => {
    set({ currentScreenshot: screenshot });
  },

  // 设置当前应用
  setCurrentApp: (app) => {
    set({ currentApp: app });
  },

  sendCommand: async (command: string) => {
    const state = get();
    if (!state.currentDeviceId) {
      agentStoreLogger.error('[sendCommand] No device selected');
      set({ error: 'No device selected' });
      return;
    }

    agentStoreLogger.info('[sendCommand] Starting command execution', {
      deviceId: state.currentDeviceId,
      command: command.substring(0, 50),
      mode: state.mode,
    });

    set({
      error: null,
      currentInstruction: command,
      pendingAction: null,
      waitingForConfirm: false,
      waitingConfirmPhase: null,
    });

    // 添加用户消息到对话历史
    state.addUserMessage(command);

    try {
      const session = await agentApi.createSession(state.currentDeviceId);
      agentStoreLogger.debug('[sendCommand] Session created', { session });

      const result = await agentApi.startTask(state.currentDeviceId, {
        task_id: state.currentTaskId || '',
        instruction: command,
        mode: state.mode,
        max_steps: state.maxSteps,
        max_parse_retries: state.maxParseRetries,
      });

      agentStoreLogger.info('[sendCommand] Task starting', {
        taskId: result.task_id,
        deviceId: state.currentDeviceId,
      });

      set({
        currentTaskId: result.task_id,
        isRunning: hasActiveBackendTask(result.task_id, result.status, true),
        status: normalizeStoreStatus(result.status),
        currentStepNum: 0,
        history: [],
        currentStep: null,
        currentScreenshot: null,
        currentApp: '未知',
        canInterrupt: true,
        canResume: false,
      });

      // Task execution is now driven by WebSocket messages (agent_step, agent_status)
      // No more polling/executeStepsLoop

    } catch (error: any) {
      agentStoreLogger.error('[sendCommand] Failed to send command', {
        error: error.message || 'Unknown error',
        status: error.response?.status,
      });

      // 如果是400错误（设备不空闲），刷新设备状态
      if (error.response?.status === 400) {
        agentStoreLogger.warn('[sendCommand] Device not idle, refreshing device status');
        useDeviceStore.getState().fetchDevices();
      }

      // 添加错误消息到对话历史
      state.addAgentMessage(`执行失败: ${error.message || '未知错误'}`);

      set({ error: error.message || 'Failed to send command', isRunning: false });
    }
  },

  appendStep: (step) => {
    set((state) => ({
      history: [...state.history, step],
    }));
  },

  setCurrentStep: (step) => {
    set({ currentStep: step });
  },

  setPendingAction: (action) => {
    set({ pendingAction: action });
    if (action) {
      set({ currentStep: action.step });
    }
  },

  // Internal handler for agent_step WebSocket message
  _handleAgentStep: (message: WsConsoleMessage) => {
    const state = get();
    const { step_number, action, reasoning, result, screenshot, success } = message;

    // 更新截图
    if (screenshot) {
      state.setScreenshot(screenshot);
    }

    // Update current step
    const step: AgentStep = {
      id: `step_${step_number}`,
      phase: 'action',
      action: {
        type: (action?.action || 'unknown') as any,
        params: action || {},
        description: formatActionDescription(action),
      },
      thinking: reasoning || '',
      timestamp: new Date().toISOString(),
      success: success ?? true,
      step_number: step_number || 0,
    };

    state.setCurrentStep(step);
    state.appendStep(step);
    set({ currentStepNum: step_number || 0 });

    // Update progress
    useDeviceStore.getState().updateDevice(state.currentDeviceId!, { status: 'busy' });

    // Add agent message
    if (action && Object.keys(action).length > 0) {
      state.addAgentMessage(
        formatActionDescription(action),
        reasoning,
        {
          type: (action?.action || 'unknown') as any,
          params: action || {},
          description: formatActionDescription(action),
        },
        screenshot
      );
    } else if (result) {
      state.addAgentMessage(result, reasoning, undefined, screenshot);
    }

    agentStoreLogger.debug('[handleAgentStep] Step processed', {
      stepNumber: step_number,
      hasAction: !!action,
      success,
    });
  },

  // Internal handler for agent_status WebSocket message
  _handleAgentStatus: (message: WsConsoleMessage) => {
    const state = get();
    const { status, message: statusMessage } = message;

    agentStoreLogger.info('[handleAgentStatus] Status update', { status, message: statusMessage });

    if (status === 'finished' || status === 'completed') {
      state.setCurrentStep(null);
      set({
        isRunning: false,
        status: 'completed',
        canInterrupt: false,
        canResume: false,
      });
      useDeviceStore.getState().updateDevice(state.currentDeviceId!, { status: 'idle' });

      if (statusMessage) {
        state.addAgentMessage(statusMessage);
      }

      // 检查是否需要遗忘
      state.forgetOldMessages();
    } else if (status === 'error' || status === 'failed') {
      state.setCurrentStep(null);
      set({
        isRunning: false,
        status: 'failed',
        error: statusMessage || 'Task failed',
        canInterrupt: false,
        canResume: false,
      });
      useDeviceStore.getState().updateDevice(state.currentDeviceId!, { status: 'error' });

      if (statusMessage) {
        state.addAgentMessage(`执行失败: ${statusMessage}`);
      }
    } else if (status === 'interrupted') {
      set({
        isRunning: false,
        status: 'interrupted',
        canInterrupt: false,
        canResume: false,
      });

      const currentDevice = state.currentDeviceId ? useDeviceStore.getState().getDeviceById(state.currentDeviceId) : undefined;
      useDeviceStore.getState().updateDevice(state.currentDeviceId!, {
        status: currentDevice?.status === 'offline' ? 'offline' : 'idle',
      });

      state.addAgentMessage(statusMessage || '任务被中断');
    } else if (status === 'pending') {
      set({
        isRunning: hasActiveBackendTask(state.currentTaskId, status, state.canInterrupt),
        status: 'pending',
        canInterrupt: Boolean(state.currentTaskId),
        canResume: false,
      });
    } else if (status === 'running') {
      set({
        isRunning: hasActiveBackendTask(state.currentTaskId, status, true),
        status: 'running',
        canInterrupt: Boolean(state.currentTaskId),
        canResume: false,
      });
    }
  },

  // Internal handler for agent_action_pending WebSocket message
  _handleActionPending: (message: WsConsoleMessage) => {
    const state = get();
    const { step_number, action, reasoning } = message;

    const pendingMessage = `即将执行: ${action?.action || 'tap'} - ${JSON.stringify(action || {})}`
    state.addAgentMessage(pendingMessage, reasoning, {
      type: (action?.action || 'tap') as any,
      params: action || {},
      description: pendingMessage,
    }, message.screenshot);

    state.setPendingAction({
      step: {
        id: `step_${step_number}`,
        phase: 'action',
        action: {
          type: (action?.action || 'tap') as any,
          params: action || {},
          description: pendingMessage,
        },
        timestamp: new Date().toISOString(),
        success: true,
        step_number: step_number || 0,
      },
    });

    set({
      isRunning: false,
      waitingForConfirm: true,
      waitingConfirmPhase: 'act',
    });
  },

  // Internal handler for session_locked WebSocket message
  _handleSessionLocked: (message: WsConsoleMessage) => {
    const state = get();
    const { controller_id } = message;

    const isThisConsole = controller_id === wsConsoleApi.getConsoleId();
    set({
      isLocked: isThisConsole,
      controllerId: controller_id,
    });

    if (!isThisConsole) {
      state.addAgentMessage('主控权被其他控制台占用，请等待...');
    }
  },

  // Internal handler for session_released WebSocket message
  _handleSessionReleased: (_message: WsConsoleMessage) => {
    const state = get();
    set({
      isLocked: true,
      controllerId: wsConsoleApi.getConsoleId(),
    });

    state.addAgentMessage('主控权已释放，可以继续操作');
  },

  // Internal handler for agent_phase_start WebSocket message
  _handlePhaseStart: (message: WsConsoleMessage) => {
    const phase = message.phase as 'reason' | 'act' | 'observe';
    agentStoreLogger.info('[handlePhaseStart] Phase started', { phase, stepNumber: message.step_number });

    set({
      currentPhase: phase,
      isThinking: phase === 'reason',
      thinkingContent: phase === 'reason' ? '思考中...' : '',
    });
  },

  // Internal handler for agent_phase_end WebSocket message
  _handlePhaseEnd: (message: WsConsoleMessage) => {
    const phase = message.phase as 'reason' | 'act' | 'observe';
    agentStoreLogger.info('[handlePhaseEnd] Phase ended', { phase, stepNumber: message.step_number });

    set({
      currentPhase: phase,
      isThinking: false,
      thinkingContent: '',
    });
  },

  // Internal handler for agent_thinking WebSocket message
  _handleThinking: (message: WsConsoleMessage) => {
    agentStoreLogger.debug('[handleThinking] Thinking update', { thinking: message.thinking });

    set({ thinkingContent: message.thinking || '' });
  },

  // Phase confirmation (cautious mode)
  confirmPhase: (approved: boolean) => {
    const state = get();
    if (!state.currentDeviceId) return;

    const phaseToConfirm = state.waitingConfirmPhase;
    agentStoreLogger.info('[confirmPhase] Sending phase confirmation', { approved, deviceId: state.currentDeviceId, phase: phaseToConfirm });

    wsConsoleApi.sendConfirmPhase(state.currentDeviceId, approved);

    set({
      pendingAction: null,
      waitingForConfirm: false,
      waitingConfirmPhase: null,
      isRunning: approved,  // Continue running if approved
    });

    if (approved) {
      state.addAgentMessage(`用户确认执行 ${phaseToConfirm} 阶段`);
    } else {
      state.addAgentMessage(`用户取消执行 ${phaseToConfirm} 阶段`);
    }
  },

  confirmAction: () => {
    get().confirmPhase(true);
  },

  rejectAction: () => {
    get().confirmPhase(false);
  },

  skipAction: () => {
    get().confirmPhase(false);
  },

  interrupt: async () => {
    const state = get();
    if (!state.currentDeviceId) {
      set({ error: 'No device selected' });
      return;
    }

    try {
      // Send interrupt via WebSocket
      wsConsoleApi.sendInterruptTask(state.currentDeviceId, state.currentTaskId || '');

      // 添加中断消息
      state.addAgentMessage('用户中断了任务执行');

      set({
        isRunning: false,
        pendingAction: null,
        status: 'interrupted',
        canInterrupt: false,
        canResume: false,
      });
    } catch (error: any) {
      console.error('Failed to interrupt:', error);
      set({ error: error.message });
    }
  },

  resume: async () => {
    throw new Error('resume 功能暂未实现，请重新发送指令');
  },

  continueTask: async (_additionalSteps = 50) => {
    throw new Error('continueTask 功能暂未实现，请重新发送指令');
  },
}));

// 格式化动作描述
function formatActionDescription(action: Record<string, any> | undefined): string {
  if (!action) return '未知动作';

  const actionType = action.action || 'unknown';
  const params: string[] = [];

  if (action.app) params.push(`应用: ${action.app}`);
  if (action.x !== undefined && action.y !== undefined) params.push(`坐标: (${action.x}, ${action.y})`);
  if (action.x1 !== undefined && action.y1 !== undefined) params.push(`起点: (${action.x1}, ${action.y1})`);
  if (action.x2 !== undefined && action.y2 !== undefined) params.push(`终点: (${action.x2}, ${action.y2})`);
  if (action.text) params.push(`文本: ${action.text}`);
  if (action.duration) params.push(`时长: ${action.duration}`);
  if (action.message) params.push(`消息: ${action.message}`);

  if (params.length === 0) {
    return `${actionType}`;
  }
  return `${actionType} - ${params.join(', ')}`;
}
