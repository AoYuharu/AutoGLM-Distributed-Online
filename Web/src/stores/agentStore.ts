import { create } from 'zustand';
import type {
  AgentStep,
  AgentMode,
  PendingAction,
  TaskStatus,
  ChatMessage,
  AgentAction,
  ObserveDecisionState,
  ObserveErrorDecisionPayload,
  AgentStageChain,
} from '../types';
import { agentApi } from '../services/agentApi';
import { wsConsoleApi, type WsConsoleMessage } from '../services/wsConsole';
import { useDeviceStore } from './deviceStore';
import { agentStoreLogger } from '../hooks/useLogger';

const MAX_MEMORY_ROUNDS = 10; // 最大记忆轮数

function normalizeObserveErrorDecisionPayload(value: any): ObserveErrorDecisionPayload | null {
  if (!value || typeof value !== 'object') {
    return null;
  }

  if (!value.task_id || !value.device_id || typeof value.message !== 'string') {
    return null;
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

function isObserveErrorDecisionStage(stage?: string): boolean {
  return stage === 'observe_error_user_decision';
}

function isObserveErrorRetryResumedStage(stage?: string): boolean {
  return stage === 'observe_error_retry_resumed';
}

function toConversationMessage(msg: Awaited<ReturnType<typeof agentApi.getChatHistory>>['messages'][number]): ChatMessage {
  const observeErrorDecision = normalizeObserveErrorDecisionPayload(
    msg.observe_error_decision ?? (isObserveErrorDecisionStage(msg.stage) ? msg.data : undefined),
  );

  return {
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
    taskId: msg.task_id,
    stepNumber: msg.step_number ?? undefined,
    progressPhase: msg.phase as ChatMessage['progressPhase'],
    progressStage: msg.stage,
    progressMessage: msg.progress_message,
    progressStatusText: msg.progress_status_text,
    result: msg.result,
    success: msg.success,
    error: msg.error,
    errorType: msg.error_type,
    observeErrorDecision: observeErrorDecision || undefined,
    isObserveErrorDecisionCard: Boolean(observeErrorDecision),
    observeErrorDecisionResolved: isObserveErrorRetryResumedStage(msg.stage),
    isProgressMessage: Boolean(msg.stage || msg.progress_message || msg.progress_status_text),
    isCompleted: isObserveErrorDecisionStage(msg.stage)
      ? false
      : msg.success === false
        ? true
        : Boolean(msg.stage && ['observe_received', 'ack_timeout', 'observe_timeout', 'ack_rejected', 'observe_error_retry_resumed'].includes(msg.stage)),
    progressKey: msg.task_id && msg.step_number != null && msg.stage
      ? `${msg.task_id}:${msg.step_number}:${isReasonStage(msg.stage) ? 'reason' : msg.stage}`
      : undefined,
  };
}

function dedupeConversationMessages(messages: ChatMessage[]): ChatMessage[] {
  const merged = new Map<string, ChatMessage>();
  messages.forEach((message) => {
    const existing = merged.get(message.id);
    merged.set(message.id, existing ? mergeProgressMessage(existing, message) : message);
  });
  return Array.from(merged.values()).sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
}

function latestScreenshotFromConversation(messages: ChatMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].screenshot) {
      return messages[i].screenshot || null;
    }
  }
  return null;
}

function progressTrackingFromConversation(messages: ChatMessage[]): Record<string, string> {
  return messages.reduce<Record<string, string>>((acc, message) => {
    if (message.isProgressMessage && message.progressKey && !message.isCompleted) {
      acc[message.progressKey] = message.id;
    }
    return acc;
  }, {});
}

function transientStateFromConversation(messages: ChatMessage[]) {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message.isProgressMessage || message.isCompleted) {
      continue;
    }

    return {
      currentPhase: message.progressPhase || null,
      isThinking: message.progressPhase === 'reason' && Boolean(message.thinking),
      thinkingContent: message.progressPhase === 'reason' ? (message.thinking || '') : '',
    };
  }

  return resetTransientProgressState();
}

function mergeRestoredConversation(primary: ChatMessage[], secondary: ChatMessage[]): ChatMessage[] {
  return dedupeConversationMessages([...primary, ...secondary]);
}

// 从 API 加载聊天历史
async function loadConversationHistoryFromAPI(deviceId: string, taskId?: string | null): Promise<ChatMessage[]> {
  try {
    const response = await agentApi.getChatHistory(deviceId, 50, taskId || undefined);
    return response.messages.map(toConversationMessage);
  } catch (e) {
    console.error('Failed to load conversation history from API:', e);
    return [];
  }
}

function normalizeStoreStatus(status?: string): TaskStatus {
  if (
    status === 'completed'
    || status === 'failed'
    || status === 'interrupted'
    || status === 'running'
    || status === 'waiting_confirmation'
  ) {
    return status;
  }
  return 'pending';
}

function hasActiveBackendTask(taskId?: string | null, status?: string, canInterrupt?: boolean): boolean {
  return Boolean(taskId) && (
    Boolean(canInterrupt)
    || status === 'pending'
    || status === 'running'
    || status === 'waiting_confirmation'
  );
}

function deriveBackendTaskActive(taskId?: string | null, status?: string, canInterrupt?: boolean): boolean {
  return hasActiveBackendTask(taskId, status, canInterrupt);
}

const AGENT_STAGE_CHAIN_TEMPLATE: AgentStageChain['nodes'] = [
  { key: 'reason', label: 'Reason', status: 'pending' },
  { key: 'action', label: 'Action', status: 'pending' },
  { key: 'dispatch', label: 'Dispatch', status: 'pending' },
  { key: 'wait_ack', label: 'Wait ACK', status: 'pending' },
  { key: 'wait_observe', label: 'Wait Observe', status: 'pending' },
  { key: 'observe', label: 'Observe', status: 'pending' },
];

const TRANSPORT_PROGRESS_STAGES = new Set([
  'action_dispatched',
  'waiting_ack',
  'ack_received',
  'waiting_observe',
  'observe_received',
  'ack_timeout',
  'observe_timeout',
  'ack_rejected',
]);

function cloneStageChainNodes(): AgentStageChain['nodes'] {
  return AGENT_STAGE_CHAIN_TEMPLATE.map((node) => ({ ...node }));
}

function isTransportProgressStage(stage?: string | null): boolean {
  return Boolean(stage && TRANSPORT_PROGRESS_STAGES.has(stage));
}

function markStageNodeRange(
  nodes: AgentStageChain['nodes'],
  endIndex: number,
  status: 'done' | 'error',
) {
  for (let index = 0; index <= endIndex && index < nodes.length; index += 1) {
    nodes[index] = {
      ...nodes[index],
      status,
    };
  }
}

function createDefaultStageChain(): AgentStageChain {
  return {
    stepNumber: null,
    rawStage: null,
    nodes: cloneStageChainNodes(),
  };
}

function deriveStageChain(
  stepNumber: number | null,
  rawStage: string | null,
  currentPhase: 'reason' | 'act' | 'observe' | null,
  status?: string,
): AgentStageChain {
  const chain: AgentStageChain = {
    stepNumber,
    rawStage,
    nodes: cloneStageChainNodes(),
  };

  const activateNode = (key: AgentStageChain['nodes'][number]['key']) => {
    chain.nodes = chain.nodes.map((node) => (
      node.key === key
        ? { ...node, status: 'active', rawStage: rawStage || undefined }
        : node
    ));
  };

  const completeNode = (key: AgentStageChain['nodes'][number]['key']) => {
    chain.nodes = chain.nodes.map((node) => (
      node.key === key
        ? { ...node, status: 'done', rawStage: rawStage || undefined }
        : node
    ));
  };

  switch (rawStage) {
    case 'reason_start':
    case 'reason_stream':
    case 'reason':
      activateNode('reason');
      return chain;
    case 'reason_complete':
      completeNode('reason');
      activateNode('action');
      return chain;
    case 'action_dispatched':
      markStageNodeRange(chain.nodes, 1, 'done');
      activateNode('dispatch');
      return chain;
    case 'waiting_ack':
      markStageNodeRange(chain.nodes, 2, 'done');
      activateNode('wait_ack');
      return chain;
    case 'ack_received':
      markStageNodeRange(chain.nodes, 3, 'done');
      activateNode('wait_observe');
      return chain;
    case 'waiting_observe':
      markStageNodeRange(chain.nodes, 3, 'done');
      activateNode('wait_observe');
      return chain;
    case 'observe_received':
      markStageNodeRange(chain.nodes, 4, 'done');
      activateNode('observe');
      return chain;
    case 'ack_timeout':
    case 'ack_rejected':
      markStageNodeRange(chain.nodes, 2, 'done');
      chain.nodes[3] = { ...chain.nodes[3], status: 'error', rawStage: rawStage || undefined };
      return chain;
    case 'observe_timeout':
      markStageNodeRange(chain.nodes, 4, 'done');
      chain.nodes[5] = { ...chain.nodes[5], status: 'error', rawStage: rawStage || undefined };
      return chain;
    default:
      break;
  }

  if (status === 'completed') {
    chain.nodes = chain.nodes.map((node) => ({ ...node, status: 'done', rawStage: rawStage || undefined }));
    return chain;
  }

  if (status === 'failed' || status === 'error' || status === 'interrupted') {
    const fallbackIndex = currentPhase === 'observe' ? 5 : currentPhase === 'act' ? 2 : 0;
    if (fallbackIndex > 0) {
      markStageNodeRange(chain.nodes, fallbackIndex - 1, 'done');
    }
    chain.nodes[fallbackIndex] = { ...chain.nodes[fallbackIndex], status: 'error', rawStage: rawStage || undefined };
    return chain;
  }

  if (currentPhase === 'observe') {
    markStageNodeRange(chain.nodes, 4, 'done');
    activateNode('observe');
  } else if (currentPhase === 'act') {
    markStageNodeRange(chain.nodes, 1, 'done');
    activateNode('dispatch');
  } else if (currentPhase === 'reason') {
    activateNode('reason');
  }

  return chain;
}

function buildDerivedAgentStatePatch(
  patch: Partial<Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>>,
  base: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>,
): Pick<AgentState, 'isBackendTaskActive' | 'stageChain'> {
  const currentTaskId = patch.currentTaskId !== undefined ? patch.currentTaskId : base.currentTaskId;
  const status = patch.status !== undefined ? patch.status : base.status;
  const canInterrupt = patch.canInterrupt !== undefined ? patch.canInterrupt : base.canInterrupt;
  const currentStepNum = patch.currentStepNum !== undefined ? patch.currentStepNum : base.currentStepNum;
  const currentPhase = patch.currentPhase !== undefined ? patch.currentPhase : base.currentPhase;
  const isBackendTaskActive = deriveBackendTaskActive(currentTaskId, status, canInterrupt);

  return {
    isBackendTaskActive,
    stageChain: isBackendTaskActive
      ? deriveStageChain(currentStepNum, null, currentPhase, status)
      : createDefaultStageChain(),
  };
}

function buildStatePatch(
  patch: Partial<AgentState>,
  base: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>,
): Partial<AgentState> {
  const derived = buildDerivedAgentStatePatch(patch, base);
  return {
    ...derived,
    ...patch,
    isBackendTaskActive: derived.isBackendTaskActive,
  };
}

function buildStatePatchFromCurrent(state: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>, patch: Partial<AgentState>): Partial<AgentState> {
  return buildStatePatch(patch, state);
}

function buildStageChainPatchFromProgress(message: WsConsoleMessage) {
  return {
    stageChain: deriveStageChain(
      normalizeProgressStepNumber(message),
      normalizeProgressStage(message),
      normalizeProgressPhase(message),
      undefined,
    ),
  };
}

function buildStageChainPatchFromStatus(message: WsConsoleMessage, currentStepNum: number, currentPhase: 'reason' | 'act' | 'observe' | null) {
  return {
    stageChain: deriveStageChain(
      currentStepNum,
      null,
      currentPhase,
      message.status,
    ),
  };
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

function getProgressKey(taskId?: string | null, stepNumber?: number | null, stage?: string | null): string | null {
  if (!taskId || stepNumber == null) return null;
  return `${taskId}:${stepNumber}:${stage || 'progress'}`;
}

function getProgressStatusText(stage?: string): string {
  switch (stage) {
    case 'reason':
    case 'reason_start':
      return '开始调用模型';
    case 'reason_stream':
      return '模型推理中';
    case 'reason_complete':
      return 'Reason 完成';
    case 'action_dispatched':
      return '动作已下发';
    case 'waiting_ack':
      return '等待 ACK';
    case 'ack_received':
      return 'ACK 已收到';
    case 'ack_rejected':
      return 'ACK 被拒绝';
    case 'waiting_observe':
      return '等待 Observe';
    case 'observe_received':
      return 'Observe 已收到';
    case 'ack_timeout':
      return 'ACK 超时';
    case 'observe_timeout':
      return 'Observe 超时';
    case 'initial_screenshot_ack_received':
      return '已获取到ACK，等待初始截图';
    case 'requesting_initial_screenshot':
      return '初始截图请求';
    case 'initial_screenshot_received':
      return '已获取到初始截图';
    case 'observe_error_user_decision':
      return '等待用户决策';
    case 'observe_error_retry_resumed':
      return '已继续重试';
    default:
      return stage || '进行中';
  }
}

function markObserveErrorDecisionResolved(history: ChatMessage[], taskId?: string | null, messageId?: string): ChatMessage[] {
  return history.map((item) => {
    const shouldResolve = messageId
      ? item.id === messageId
      : Boolean(item.isObserveErrorDecisionCard && item.taskId && taskId && item.taskId === taskId && !item.observeErrorDecisionResolved);

    if (!shouldResolve) {
      return item;
    }

    return {
      ...item,
      observeErrorDecisionResolved: true,
      isCompleted: true,
      progressStatusText: item.progressStatusText || '已处理',
    };
  });
}

function getPendingObserveErrorDecisionFromConversation(messages: ChatMessage[]): ObserveErrorDecisionPayload | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message.isObserveErrorDecisionCard && !message.observeErrorDecisionResolved && message.observeErrorDecision) {
      return message.observeErrorDecision;
    }
  }
  return null;
}

function getObserveDecisionStateFromConversation(messages: ChatMessage[]): ObserveDecisionState {
  return getPendingObserveErrorDecisionFromConversation(messages) ? 'pending' : 'idle';
}

function buildObserveErrorDecisionAppliedMessage(message: WsConsoleMessage): string {
  if (message.success === false) {
    return message.message || '提交 Observe 错误决策失败';
  }
  return message.decision === 'interrupt'
    ? '已提交中断任务请求'
    : '已提交继续任务请求，等待下一轮推理';
}

function buildObserveErrorDecisionAppliedBubble(message: WsConsoleMessage): ChatMessage {
  return {
    id: generateId(),
    role: 'agent',
    content: buildObserveErrorDecisionAppliedMessage(message),
    timestamp: new Date().toISOString(),
    taskId: message.task_id,
    success: message.success !== false,
    error: message.success === false ? buildObserveErrorDecisionAppliedMessage(message) : undefined,
    isCompleted: true,
  };
}

function normalizeProgressDecisionPayload(message: WsConsoleMessage): ObserveErrorDecisionPayload | null {
  return normalizeObserveErrorDecisionPayload(message.observe_error_decision ?? message.data);
}

function isWaitingConfirmationStatus(status?: string): boolean {
  return status === 'waiting_confirmation';
}

function clearObserveDecisionSubmissionState() {
  return {
    observeDecisionState: 'idle' as const,
    pendingObserveErrorDecision: null,
  };
}

function buildObserveDecisionStatePatchFromHistory(history: ChatMessage[]) {
  return {
    observeDecisionState: getObserveDecisionStateFromConversation(history),
    pendingObserveErrorDecision: getPendingObserveErrorDecisionFromConversation(history),
  };
}

function buildObserveDecisionStatePatchFromProgress(message: WsConsoleMessage) {
  const payload = normalizeProgressDecisionPayload(message);
  if (isObserveErrorDecisionStage(message.stage) && payload) {
    return {
      observeDecisionState: 'pending' as const,
      pendingObserveErrorDecision: payload,
      status: 'waiting_confirmation' as const,
      isRunning: true,
      canInterrupt: true,
    };
  }

  if (isObserveErrorRetryResumedStage(message.stage)) {
    return {
      observeDecisionState: 'idle' as const,
      pendingObserveErrorDecision: null,
      status: 'running' as const,
      isRunning: true,
    };
  }

  return {};
}

function buildObserveDecisionStatePatchFromStatus(message: WsConsoleMessage) {
  if (isWaitingConfirmationStatus(message.status)) {
    return {
      observeDecisionState: 'pending' as const,
      status: 'waiting_confirmation' as const,
      isRunning: true,
    };
  }

  if (isTerminalAgentStatus(message.status) || isRunningStatus(message.status) || isPendingStatus(message.status)) {
    return clearObserveDecisionSubmissionState();
  }

  return {};
}

function normalizeStatusTaskIdSafe(message: WsConsoleMessage): string | null {
  return normalizeStatusTaskId(message);
}

function normalizeStatusWaitingConfirmationPatch(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    isRunning: true,
    status: 'waiting_confirmation' as const,
    currentTaskId: updateCurrentTaskIdFromMessage(normalizeStatusTaskIdSafe(message), currentTaskId),
    canInterrupt: Boolean(normalizeStatusTaskIdSafe(message) || currentTaskId),
    canResume: false,
  };
}

function clearObserveDecisionHistoryState(history: ChatMessage[], taskId: string | null, messageId?: string): ChatMessage[] {
  return markObserveErrorDecisionResolved(history, taskId, messageId);
}

function buildObserveErrorDecisionUserMessage(decision: 'continue' | 'interrupt', advice?: string): string {
  if (decision === 'interrupt') {
    return '用户选择中断任务';
  }
  return advice ? `用户选择继续任务，并给出建议：${advice}` : '用户选择继续任务';
}

function isObserveDecisionAppliedSuccess(message: WsConsoleMessage): boolean {
  return message.success !== false;
}

function buildObserveDecisionCardStateFromSnapshot(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, history: ChatMessage[]) {
  const restored = snapshot.pending_observe_error_prompt || getPendingObserveErrorDecisionFromConversation(history);
  return {
    observeDecisionState: snapshot.awaiting_observe_error_decision ? 'pending' as const : getObserveDecisionStateFromConversation(history),
    pendingObserveErrorDecision: snapshot.awaiting_observe_error_decision ? restored : getPendingObserveErrorDecisionFromConversation(history),
  };
}

function normalizeObserveDecisionCardMessage(payload: ObserveErrorDecisionPayload): ChatMessage {
  return {
    id: payload.message_id || generateId(),
    role: 'agent',
    content: payload.message,
    timestamp: payload.created_at || new Date().toISOString(),
    taskId: payload.task_id,
    stepNumber: payload.step_number,
    progressPhase: 'observe',
    progressStage: 'observe_error_user_decision',
    progressMessage: payload.message,
    progressStatusText: '等待用户决策',
    error: payload.message,
    errorType: payload.error_type,
    success: false,
    observeErrorDecision: payload,
    isObserveErrorDecisionCard: true,
    observeErrorDecisionResolved: false,
    isProgressMessage: true,
    isCompleted: false,
    progressKey: `${payload.task_id}:${payload.step_number}:observe_error_user_decision`,
  };
}

function ensureObserveDecisionCard(history: ChatMessage[], payload: ObserveErrorDecisionPayload | null): ChatMessage[] {
  if (!payload) {
    return history;
  }

  const existing = history.find((item) => item.id === payload.message_id || (item.isObserveErrorDecisionCard && item.taskId === payload.task_id && item.stepNumber === payload.step_number && !item.observeErrorDecisionResolved));
  if (existing) {
    return history.map((item) => item === existing ? mergeProgressMessage(item, normalizeObserveDecisionCardMessage(payload)) : item);
  }

  return [...history, normalizeObserveDecisionCardMessage(payload)];
}

function normalizeConversationAfterSnapshot(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, restoredHistory: ChatMessage[]): ChatMessage[] {
  return ensureObserveDecisionCard(restoredHistory, snapshot.awaiting_observe_error_decision ? snapshot.pending_observe_error_prompt : null);
}

function normalizeConversationForDisplay(history: ChatMessage[]): ChatMessage[] {
  return history.filter((message) => !message.isTransportProgress);
}

function annotateTransportProgressMessage(message: ChatMessage): ChatMessage {
  return {
    ...message,
    isTransportProgress: Boolean(message.isProgressMessage && isTransportProgressStage(message.progressStage)),
  };
}

function buildConversationHistoryPatch(history: ChatMessage[]) {
  const conversationHistory = history.map(annotateTransportProgressMessage);
  return {
    conversationHistory,
    displayConversationHistory: normalizeConversationForDisplay(conversationHistory),
  };
}

function getStageChainStepNumber(currentStepNum: number): number | null {
  return currentStepNum > 0 ? currentStepNum : null;
}

function deriveCurrentPhaseFromConversation(messages: ChatMessage[]): 'reason' | 'act' | 'observe' | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message.isProgressMessage || message.isCompleted) {
      continue;
    }
    return message.progressPhase || null;
  }
  return null;
}

function deriveStageChainFromConversation(messages: ChatMessage[], fallbackStepNum = 0, fallbackStatus?: string): AgentStageChain {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message.taskId || !message.isProgressMessage || message.isCompleted) {
      continue;
    }

    return deriveStageChain(
      message.stepNumber ?? getStageChainStepNumber(fallbackStepNum),
      message.progressStage || null,
      message.progressPhase || null,
      fallbackStatus,
    );
  }

  if (fallbackStatus === 'completed' || fallbackStatus === 'failed' || fallbackStatus === 'interrupted') {
    return deriveStageChain(getStageChainStepNumber(fallbackStepNum), null, null, fallbackStatus);
  }

  return createDefaultStageChain();
}

function deriveStageChainFromSnapshot(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, history: ChatMessage[]): AgentStageChain {
  if (!hasActiveBackendTask(snapshot.task_id, snapshot.status, snapshot.can_interrupt)) {
    return createDefaultStageChain();
  }

  const fromHistory = deriveStageChainFromConversation(history, snapshot.current_step, snapshot.status);
  if (fromHistory.rawStage || fromHistory.stepNumber !== null) {
    return fromHistory;
  }

  return deriveStageChain(
    getStageChainStepNumber(snapshot.current_step),
    null,
    deriveCurrentPhaseFromConversation(history),
    snapshot.status,
  );
}

function buildStatePatchWithConversation(
  base: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>,
  patch: Partial<AgentState> & { conversationHistory?: ChatMessage[] },
): Partial<AgentState> {
  const conversationPatch = patch.conversationHistory
    ? buildConversationHistoryPatch(patch.conversationHistory)
    : {};
  return buildStatePatch({ ...patch, ...conversationPatch }, base);
}

function buildStatePatchForProgressMessage(
  state: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>,
  patch: Partial<AgentState>,
  message: WsConsoleMessage,
): Partial<AgentState> {
  return buildStatePatch({
    ...patch,
    stageChain: deriveStageChain(
      normalizeProgressStepNumber(message),
      normalizeProgressStage(message),
      normalizeProgressPhase(message),
      patch.status ?? state.status,
    ),
  }, state);
}

function buildStatePatchForStatusMessage(
  state: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>,
  patch: Partial<AgentState>,
  message: WsConsoleMessage,
): Partial<AgentState> {
  return buildStatePatch({
    ...patch,
    stageChain: deriveStageChain(
      getStageChainStepNumber(state.currentStepNum),
      null,
      patch.currentPhase ?? state.currentPhase,
      message.status,
    ),
  }, state);
}

function buildStatePatchForSnapshot(
  patch: Partial<AgentState>,
  snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>,
  history: ChatMessage[],
): Partial<AgentState> {
  const currentPhase = deriveCurrentPhaseFromConversation(history);
  return {
    ...buildStatePatch({
      ...patch,
      currentPhase,
    }, {
      currentTaskId: snapshot.task_id,
      status: normalizeStoreStatus(snapshot.status),
      canInterrupt: snapshot.can_interrupt,
      currentStepNum: snapshot.current_step,
      currentPhase,
    }),
    stageChain: deriveStageChainFromSnapshot(snapshot, history),
  };
}

function buildResetStatePatch(
  base: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>,
  patch: Partial<AgentState>,
): Partial<AgentState> {
  return buildStatePatch({
    ...patch,
    stageChain: createDefaultStageChain(),
  }, base);
}

function withTransportMetadata(message: ChatMessage): ChatMessage {
  return annotateTransportProgressMessage(message);
}

function withTransportMetadataUpdates(message: Partial<ChatMessage>): Partial<ChatMessage> {
  return message.progressStage
    ? { ...message, isTransportProgress: isTransportProgressStage(message.progressStage) }
    : message;
}

function buildCurrentPhaseAndStageChainPatch(history: ChatMessage[], fallbackStepNum = 0, fallbackStatus?: string) {
  const currentPhase = deriveCurrentPhaseFromConversation(history);
  return {
    currentPhase,
    stageChain: deriveStageChainFromConversation(history, fallbackStepNum, fallbackStatus),
  };
}

function shouldCreateVisibleStepBubble(): boolean {
  return true;
}

function shouldCreateVisibleStatusBubble(): boolean {
  return true;
}

function shouldCreateVisibleProgressBubble(message: WsConsoleMessage): boolean {
  return !isTransportProgressStage(normalizeProgressStage(message));
}

function buildDerivedFlagsPatch(taskId?: string | null, status?: string, canInterrupt?: boolean) {
  return {
    isBackendTaskActive: deriveBackendTaskActive(taskId, status, canInterrupt),
  };
}

function buildSnapshotDerivedPatch(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, history: ChatMessage[]) {
  return {
    ...buildDerivedFlagsPatch(snapshot.task_id, normalizeStoreStatus(snapshot.status), snapshot.can_interrupt),
    ...buildCurrentPhaseAndStageChainPatch(history, snapshot.current_step, snapshot.status),
  };
}

function buildConversationAndDerivedPatch(
  state: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>,
  history: ChatMessage[],
  extraPatch: Partial<AgentState> = {},
): Partial<AgentState> {
  return buildStatePatchWithConversation(state, {
    ...extraPatch,
    ...buildCurrentPhaseAndStageChainPatch(history, extraPatch.currentStepNum ?? state.currentStepNum, extraPatch.status ?? state.status),
    conversationHistory: history,
  });
}

function buildTaskCreatedDerivedStatePatch(
  base: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>,
  patch: Partial<AgentState>,
): Partial<AgentState> {
  return buildStatePatch({
    ...patch,
    stageChain: createDefaultStageChain(),
  }, base);
}

function buildBackendActivityStatePatch(taskId?: string | null, status?: string, canInterrupt?: boolean) {
  return buildDerivedFlagsPatch(taskId, status, canInterrupt);
}

function buildVisibleConversationStatePatch(history: ChatMessage[]) {
  return {
    displayConversationHistory: normalizeConversationForDisplay(history),
  };
}

function buildCurrentStageChainStatePatch(state: Pick<AgentState, 'currentStepNum' | 'currentPhase' | 'status'>) {
  return {
    stageChain: deriveStageChain(getStageChainStepNumber(state.currentStepNum), null, state.currentPhase, state.status),
  };
}

function buildSnapshotConversationStatePatch(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, history: ChatMessage[]) {
  return {
    ...buildConversationHistoryPatch(history),
    ...buildSnapshotDerivedPatch(snapshot, history),
  };
}

function buildIdleDerivedPatch() {
  return {
    isBackendTaskActive: false,
    stageChain: createDefaultStageChain(),
  };
}

function buildTransportAwareProgressMessage(message: WsConsoleMessage, progressKey: string): ChatMessage {
  return withTransportMetadata(buildProgressBaseMessage(message, progressKey));
}

function buildTransportAwareStepMessage(message: WsConsoleMessage): ChatMessage {
  return withTransportMetadata(buildStandaloneAgentStepMessage(message));
}

function buildTransportAwareStatusMessage(message: WsConsoleMessage): ChatMessage {
  return withTransportMetadata(buildStandaloneStatusMessage(message));
}

function buildTransportAwareProgressUpdates(message: WsConsoleMessage): Partial<ChatMessage> {
  return withTransportMetadataUpdates(buildProgressConversationUpdates(message));
}

function deriveDisplayConversationHistory(history: ChatMessage[]) {
  return normalizeConversationForDisplay(history);
}

function buildDisplayConversationPatch(history: ChatMessage[]) {
  return {
    displayConversationHistory: deriveDisplayConversationHistory(history),
  };
}

function buildActiveStageChainPatch(state: Pick<AgentState, 'currentStepNum' | 'currentPhase' | 'status'>) {
  return buildCurrentStageChainStatePatch(state);
}

function buildBackendTruthPatch(taskId?: string | null, status?: string, canInterrupt?: boolean) {
  return buildBackendActivityStatePatch(taskId, status, canInterrupt);
}

function buildHydratedHistoryPatch(history: ChatMessage[]) {
  return buildConversationHistoryPatch(history);
}

function buildMessageVisibilityPatch(history: ChatMessage[]) {
  return buildVisibleConversationStatePatch(history);
}

function buildSnapshotInitPatch(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, history: ChatMessage[]) {
  return buildSnapshotConversationStatePatch(snapshot, history);
}

function buildTaskActiveDerivedPatch(taskId?: string | null, status?: string, canInterrupt?: boolean) {
  return buildBackendTruthPatch(taskId, status, canInterrupt);
}

function buildDisplayDerivedPatch(history: ChatMessage[]) {
  return buildDisplayConversationPatch(history);
}

function buildDerivedPatchFromConversation(history: ChatMessage[]) {
  return buildConversationHistoryPatch(history);
}

function buildTaskCreatedVisibilityPatch(history: ChatMessage[]) {
  return buildVisibleConversationStatePatch(history);
}

function buildSnapshotHydratedPatch(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, history: ChatMessage[]) {
  return buildSnapshotInitPatch(snapshot, history);
}

function buildCurrentStageChainPatch(message: WsConsoleMessage) {
  return {
    stageChain: deriveStageChain(
      normalizeProgressStepNumber(message),
      normalizeProgressStage(message),
      normalizeProgressPhase(message),
      undefined,
    ),
  };
}

function buildDisplayHistoryPatch(history: ChatMessage[]) {
  return buildVisibleConversationStatePatch(history);
}

function buildConversationUIStatePatch(history: ChatMessage[]) {
  return buildDisplayHistoryPatch(history);
}

function buildConversationFilterStatePatch(history: ChatMessage[]) {
  return buildVisibleConversationStatePatch(history);
}

function buildSnapshotUIStatePatch(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, history: ChatMessage[]) {
  return buildSnapshotHydratedPatch(snapshot, history);
}

function buildBackendActivityPatch(taskId?: string | null, status?: string, canInterrupt?: boolean) {
  return buildBackendActivityStatePatch(taskId, status, canInterrupt);
}

function buildDerivedResetPatch() {
  return buildIdleDerivedPatch();
}

function buildResetPatch(
  base: Pick<AgentState, 'currentTaskId' | 'status' | 'canInterrupt' | 'currentStepNum' | 'currentPhase'>,
  patch: Partial<AgentState>,
) {
  return buildResetStatePatch(base, patch);
}

function buildConversationVisibilityPatch(history: ChatMessage[]) {
  return {
    ...buildConversationHistoryPatch(history),
    ...buildVisibleConversationStatePatch(history),
  };
}

function buildActiveStatePatch(taskId?: string | null, status?: string, canInterrupt?: boolean) {
  return buildBackendActivityStatePatch(taskId, status, canInterrupt);
}

function buildCurrentDerivedStatePatch(state: Pick<AgentState, 'currentStepNum' | 'currentPhase' | 'status'>) {
  return buildActiveStageChainPatch(state);
}

function buildSnapshotRestorePatch(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, history: ChatMessage[]) {
  return buildSnapshotInitPatch(snapshot, history);
}

function buildVisibleHistoryPatch(history: ChatMessage[]) {
  return buildVisibleConversationStatePatch(history);
}

function buildTaskActivityPatch(taskId?: string | null, status?: string, canInterrupt?: boolean) {
  return buildBackendActivityStatePatch(taskId, status, canInterrupt);
}

function buildFilteredConversationStatePatch(history: ChatMessage[]) {
  return buildVisibleConversationStatePatch(history);
}

function buildActivityDerivedPatch(taskId?: string | null, status?: string, canInterrupt?: boolean) {
  return buildBackendActivityStatePatch(taskId, status, canInterrupt);
}

function normalizeProgressStateWithObserveDecision(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    ...normalizeAgentProgressPatch(message, currentTaskId),
    ...buildObserveDecisionStatePatchFromProgress(message),
  };
}

function normalizeStatusStateWithObserveDecision(message: WsConsoleMessage, currentTaskId: string | null, canInterrupt: boolean) {
  if (isWaitingConfirmationStatus(message.status)) {
    return normalizeStatusWaitingConfirmationPatch(message, currentTaskId);
  }
  return {
    ...normalizeAgentStatusPatch(message, currentTaskId, canInterrupt),
    ...buildObserveDecisionStatePatchFromStatus(message),
  };
}

function normalizeTaskCreatedObserveDecisionState() {
  return clearObserveDecisionSubmissionState();
}

function normalizeSendCommandObserveDecisionState() {
  return clearObserveDecisionSubmissionState();
}

function normalizeInterruptObserveDecisionState() {
  return clearObserveDecisionSubmissionState();
}

function normalizeEndSessionObserveDecisionState() {
  return clearObserveDecisionSubmissionState();
}

function normalizeInitObserveDecisionState(snapshot: Awaited<ReturnType<typeof agentApi.getLatestDeviceSnapshot>>, history: ChatMessage[]) {
  return buildObserveDecisionCardStateFromSnapshot(snapshot, history);
}

function normalizeObserveDecisionStateAfterResolution(messageId?: string, taskId?: string | null) {
  return {
    messageId,
    taskId: taskId || null,
  };
}

function mergeProgressMessage(message: ChatMessage, updates: Partial<ChatMessage>): ChatMessage {
  return {
    ...message,
    ...updates,
    thinking: updates.thinking ?? message.thinking,
    action: updates.action ?? message.action,
    screenshot: updates.screenshot ?? message.screenshot,
    result: updates.result ?? message.result,
    error: updates.error ?? message.error,
    errorType: updates.errorType ?? message.errorType,
    progressStatusText: updates.progressStatusText ?? message.progressStatusText,
    progressMessage: updates.progressMessage ?? message.progressMessage,
    observeErrorDecision: updates.observeErrorDecision ?? message.observeErrorDecision,
    isObserveErrorDecisionCard: updates.isObserveErrorDecisionCard ?? message.isObserveErrorDecisionCard,
    observeErrorDecisionResolved: updates.observeErrorDecisionResolved ?? message.observeErrorDecisionResolved,
  };
}

function isReasonStage(stage?: string | null): boolean {
  return stage === 'reason_start' || stage === 'reason_stream' || stage === 'reason_complete' || stage === 'reason';
}

function getProgressContent(message: WsConsoleMessage): string {
  if (message.error) {
    return `❌ ${message.error}`;
  }
  if (message.result) {
    return message.result;
  }
  return message.message || getProgressStatusText(message.stage);
}

function normalizeAction(action?: Record<string, any>): AgentAction | undefined {
  if (!action || Object.keys(action).length === 0) return undefined;
  return {
    type: (action.action || 'unknown') as any,
    params: action,
    description: formatActionDescription(action),
  };
}

function buildStatusFailureMessage(statusMessage?: string, data?: Record<string, any>): string {
  const errorType = data?.error_type;
  if (errorType === 'ack_timeout') {
    return '执行失败: ACK 超时';
  }
  if (errorType === 'observe_timeout') {
    return '执行失败: Observe 超时';
  }
  if (statusMessage === 'Device not found') {
    return '执行失败: Device not found (ACK_TIMEOUT)';
  }
  return `执行失败: ${statusMessage || 'Task failed'}`;
}

function buildStatusCompletionMessage(statusMessage?: string): string {
  return statusMessage || 'Task completed';
}

function buildStatusInterruptionMessage(statusMessage?: string, data?: Record<string, any>): string {
  if (data?.reason === 'device_disconnected') {
    return statusMessage || '设备连接断开，任务已中断';
  }
  return statusMessage || '任务被中断';
}


function upsertConversationProgress(
  history: ChatMessage[],
  messageId: string,
  baseMessage: ChatMessage,
): ChatMessage[] {
  const exists = history.some((msg) => msg.id === messageId);
  if (!exists) {
    return [...history, baseMessage];
  }

  return history.map((msg) => (msg.id === messageId ? mergeProgressMessage(msg, baseMessage) : msg));
}

function finalizeProgressMessages(
  history: ChatMessage[],
  taskId: string | null,
  status: 'completed' | 'failed' | 'interrupted',
  content?: string,
  errorType?: string,
): ChatMessage[] {
  if (!taskId) return history;

  return history.map((msg) => {
    if (msg.taskId !== taskId || !msg.isProgressMessage || msg.isCompleted) {
      return msg;
    }

    const isFailure = status === 'failed' || status === 'interrupted';
    return {
      ...msg,
      content: content || msg.content,
      progressStatusText: status === 'completed' ? '已完成' : status === 'failed' ? '已失败' : '已中断',
      errorType: errorType || msg.errorType,
      success: status === 'completed',
      isCompleted: true,
      error: isFailure ? (content || msg.error) : msg.error,
    };
  });
}

function clearProgressTracking(progressMessageIds: Record<string, string>, taskId: string | null): Record<string, string> {
  if (!taskId) return progressMessageIds;

  return Object.fromEntries(
    Object.entries(progressMessageIds).filter(([key]) => !key.startsWith(`${taskId}:`))
  );
}

function appendStatusMessageIfNeeded(
  history: ChatMessage[],
  taskId: string | null,
  content: string,
  status: 'completed' | 'failed' | 'interrupted',
): ChatMessage[] {
  if (!taskId) return history;
  const hasOpenProgress = history.some((msg) => msg.taskId === taskId && msg.isProgressMessage && !msg.isCompleted);
  if (hasOpenProgress) {
    return history;
  }

  return [
    ...history,
    {
      id: generateId(),
      role: 'agent',
      content,
      timestamp: new Date().toISOString(),
      taskId,
      success: status === 'completed',
      error: status === 'completed' ? undefined : content,
      errorType: status === 'failed' ? 'task_failed' : undefined,
      isCompleted: true,
    },
  ];
}

function shouldMarkProgressCompleted(stage?: string): boolean {
  return stage === 'observe_received' || stage === 'ack_timeout' || stage === 'observe_timeout' || stage === 'ack_rejected';
}

function shouldKeepThinkingPanel(stage?: string): boolean {
  return stage === 'reason_complete';
}

function inferProgressPhase(message: WsConsoleMessage): 'reason' | 'act' | 'observe' {
  return (message.phase as 'reason' | 'act' | 'observe') || 'act';
}

function inferMessageSuccess(message: WsConsoleMessage): boolean | undefined {
  if (message.success !== undefined) return message.success;
  if (message.stage === 'ack_timeout' || message.stage === 'observe_timeout' || message.stage === 'ack_rejected') {
    return false;
  }
  if (message.stage === 'observe_received') {
    return message.error ? false : true;
  }
  return undefined;
}

function inferMessageError(message: WsConsoleMessage): string | undefined {
  return message.error || (inferMessageSuccess(message) === false ? getProgressStatusText(message.stage) : undefined);
}

function inferProgressTimestamp(): string {
  return new Date().toISOString();
}

function isSameProgressMessage(msg: ChatMessage, progressKey: string): boolean {
  return msg.progressKey === progressKey || msg.id === progressKey;
}

function updateProgressHistoryByKey(
  history: ChatMessage[],
  progressKey: string,
  updater: (msg: ChatMessage) => ChatMessage,
): ChatMessage[] {
  return history.map((msg) => (isSameProgressMessage(msg, progressKey) ? updater(msg) : msg));
}

function hasProgressMessage(history: ChatMessage[], progressKey: string): boolean {
  return history.some((msg) => isSameProgressMessage(msg, progressKey));
}

function buildProgressMessageId(progressKey: string): string {
  return progressKey;
}

function getTaskIdFromMessage(message: WsConsoleMessage): string | null {
  return message.task_id || null;
}

function getStepNumberFromMessage(message: WsConsoleMessage): number {
  return message.step_number ?? 0;
}

function shouldFinalizeOnAgentStep(message: WsConsoleMessage): boolean {
  return message.step_number != null && Boolean(message.task_id);
}

function finalizeProgressForStep(
  history: ChatMessage[],
  progressKey: string,
  updates: Partial<ChatMessage>,
): ChatMessage[] {
  return updateProgressHistoryByKey(history, progressKey, (msg) => ({
    ...mergeProgressMessage(msg, updates),
    isCompleted: true,
  }));
}

function normalizeStatusData(data?: Record<string, any>): Record<string, any> {
  return data || {};
}

function normalizeFailureErrorType(message: WsConsoleMessage): string | undefined {
  return message.error_type || message.data?.error_type;
}

function normalizeFailureMessage(message: WsConsoleMessage): string {
  return buildStatusFailureMessage(message.message, normalizeStatusData(message.data));
}

function normalizeCompletionMessage(message: WsConsoleMessage): string {
  return buildStatusCompletionMessage(message.message);
}

function normalizeInterruptionMessage(message: WsConsoleMessage): string {
  return buildStatusInterruptionMessage(message.message, normalizeStatusData(message.data));
}

function progressHistoryAfterStatus(
  history: ChatMessage[],
  taskId: string | null,
  status: 'completed' | 'failed' | 'interrupted',
  content: string,
  errorType?: string,
): ChatMessage[] {
  const finalized = finalizeProgressMessages(history, taskId, status, content, errorType);
  return appendStatusMessageIfNeeded(finalized, taskId, content, status);
}

function shouldSuppressStandaloneAgentStep(history: ChatMessage[], progressKey: string | null): boolean {
  return Boolean(progressKey && hasProgressMessage(history, progressKey));
}

function buildAgentStepMessageContent(action?: Record<string, any>, result?: string, success?: boolean, error?: string): string {
  if (success === false) {
    return `❌ ${error || result || '执行失败'}`;
  }
  if (action && Object.keys(action).length > 0) {
    return formatActionDescription(action);
  }
  return result || '步骤完成';
}


function updateProgressTracking(
  map: Record<string, string>,
  progressKey: string,
  messageId: string,
): Record<string, string> {
  return {
    ...map,
    [progressKey]: messageId,
  };
}

function removeProgressTracking(
  map: Record<string, string>,
  progressKey?: string | null,
): Record<string, string> {
  if (!progressKey) return map;
  const next = { ...map };
  delete next[progressKey];
  return next;
}

function removeTaskProgressTracking(
  map: Record<string, string>,
  taskId: string | null,
): Record<string, string> {
  return clearProgressTracking(map, taskId);
}

function normalizeProgressResult(message: WsConsoleMessage): string | undefined {
  return message.result || undefined;
}

function normalizeProgressScreenshot(message: WsConsoleMessage): string | undefined {
  return message.screenshot || undefined;
}

function normalizeProgressErrorType(message: WsConsoleMessage): string | undefined {
  return message.error_type || undefined;
}

function normalizeProgressAction(message: WsConsoleMessage): AgentAction | undefined {
  return normalizeAction(message.action);
}

function normalizeProgressThinking(message: WsConsoleMessage): string | undefined {
  return message.reasoning || undefined;
}

function normalizeProgressMessageText(message: WsConsoleMessage): string {
  return message.message || getProgressStatusText(message.stage);
}

function normalizeProgressStage(message: WsConsoleMessage): string {
  return message.stage || 'progress';
}

function normalizeProgressTaskId(message: WsConsoleMessage): string | null {
  return getTaskIdFromMessage(message);
}

function normalizeProgressStepNumber(message: WsConsoleMessage): number {
  return getStepNumberFromMessage(message);
}

function normalizeProgressKey(message: WsConsoleMessage): string | null {
  // Collapse all reason-related stages into a single evolving bubble.
  // reason_start / reason_stream / reason_complete all use the same :reason key.
  const rawStage = normalizeProgressStage(message);
  const collapsedStage = isReasonStage(rawStage) ? 'reason' : rawStage;
  return getProgressKey(
    normalizeProgressTaskId(message) || undefined,
    normalizeProgressStepNumber(message),
    collapsedStage,
  );
}


function shouldFinalizeProgressMessage(message: WsConsoleMessage): boolean {
  return shouldMarkProgressCompleted(message.stage);
}

function buildProgressBubbleContent(message: WsConsoleMessage): string {
  return getProgressContent(message);
}

function normalizeProgressStatus(message: WsConsoleMessage): string {
  return getProgressStatusText(message.stage);
}

function normalizeProgressSuccess(message: WsConsoleMessage): boolean | undefined {
  return inferMessageSuccess(message);
}

function normalizeProgressError(message: WsConsoleMessage): string | undefined {
  return inferMessageError(message);
}

function normalizeProgressPhase(message: WsConsoleMessage): 'reason' | 'act' | 'observe' {
  return inferProgressPhase(message);
}

function normalizeProgressTimestampValue(): string {
  return inferProgressTimestamp();
}

function shouldHideThinkingPanel(message: WsConsoleMessage): boolean {
  return !shouldKeepThinkingPanel(message.stage);
}

function normalizeStatusTaskId(message: WsConsoleMessage): string | null {
  return message.task_id || message.data?.task_id || null;
}

function normalizeAgentStepProgressKey(message: WsConsoleMessage): string | null {
  // Apply same reason-stage normalization so agent_step is suppressed if a reason bubble exists.
  const rawStage = message.stage || 'observe_received';
  const collapsedStage = isReasonStage(rawStage) ? 'reason' : rawStage;
  return getProgressKey(message.task_id, message.step_number, collapsedStage);
}

function getAnyStepProgressKeys(progressMessageIds: Record<string, string>, message: WsConsoleMessage): string[] {
  const taskId = message.task_id;
  const stepNumber = message.step_number;
  if (!taskId || stepNumber == null) {
    return [];
  }

  const prefix = `${taskId}:${stepNumber}:`;
  return Object.keys(progressMessageIds).filter((key) => key.startsWith(prefix));
}

function hasAnyStepProgressMessage(state: { conversationHistory: ChatMessage[] }, message: WsConsoleMessage): boolean {
  const taskId = message.task_id;
  const stepNumber = message.step_number;
  if (!taskId || stepNumber == null) {
    return false;
  }

  const prefix = `${taskId}:${stepNumber}:`;
  return state.conversationHistory.some((item) => item.progressKey?.startsWith(prefix));
}

function markAllStepProgressCompleted(
  history: ChatMessage[],
  message: WsConsoleMessage,
): ChatMessage[] {
  const taskId = message.task_id;
  const stepNumber = message.step_number;
  if (!taskId || stepNumber == null) {
    return history;
  }

  const prefix = `${taskId}:${stepNumber}:`;
  return history.map((item) => {
    if (!item.progressKey?.startsWith(prefix)) {
      return item;
    }

    return mergeProgressMessage(item, {
      thinking: normalizeAgentStepThinking(message),
      action: normalizeAgentStepAction(message),
      screenshot: normalizeAgentStepScreenshot(message),
      result: normalizeAgentStepResult(message),
      success: normalizeAgentStepSuccess(message),
      error: normalizeAgentStepError(message),
      errorType: normalizeAgentStepErrorType(message),
      isCompleted: true,
      progressStatusText: item.progressStatusText || (message.success === false ? '已失败' : '已完成'),
    });
  });
}

function removeAllStepProgressTracking(
  progressMessageIds: Record<string, string>,
  message: WsConsoleMessage,
): Record<string, string> {
  return getAnyStepProgressKeys(progressMessageIds, message).reduce<Record<string, string>>((acc, key) => {
    const next = { ...acc };
    delete next[key];
    return next;
  }, { ...progressMessageIds });
}

function latestOpenProgressMessageForStep(state: { conversationHistory: ChatMessage[] }, message: WsConsoleMessage): ChatMessage | undefined {
  const taskId = message.task_id;
  const stepNumber = message.step_number;
  if (!taskId || stepNumber == null) {
    return undefined;
  }

  const prefix = `${taskId}:${stepNumber}:`;
  return [...state.conversationHistory]
    .reverse()
    .find((item) => item.progressKey?.startsWith(prefix) && !item.isCompleted);
}

function shouldSyncProgressScreenshotOnStep(state: { conversationHistory: ChatMessage[] }, message: WsConsoleMessage): boolean {
  return Boolean(latestOpenProgressMessageForStep(state, message)?.screenshot);
}

function screenshotForStepMessage(state: { conversationHistory: ChatMessage[] }, message: WsConsoleMessage): string | undefined {
  return message.screenshot || latestOpenProgressMessageForStep(state, message)?.screenshot;
}

function normalizeAgentStepAction(message: WsConsoleMessage): AgentAction | undefined {
  return normalizeAction(message.action);
}

function normalizeAgentStepThinking(message: WsConsoleMessage): string | undefined {
  return message.reasoning || undefined;
}

function normalizeAgentStepScreenshot(message: WsConsoleMessage): string | undefined {
  return message.screenshot || undefined;
}

function normalizeAgentStepResult(message: WsConsoleMessage): string | undefined {
  return message.result || undefined;
}

function normalizeAgentStepError(message: WsConsoleMessage): string | undefined {
  return message.error || undefined;
}

function normalizeAgentStepErrorType(message: WsConsoleMessage): string | undefined {
  return message.error_type || undefined;
}

function normalizeAgentStepSuccess(message: WsConsoleMessage): boolean {
  return message.success ?? true;
}

function normalizeAgentStepContent(message: WsConsoleMessage): string {
  return buildAgentStepMessageContent(message.action, message.result, message.success, message.error);
}

function normalizeAgentStepTimestamp(): string {
  return new Date().toISOString();
}

function normalizeAgentStepNumber(message: WsConsoleMessage): number {
  return message.step_number ?? 0;
}

function normalizeAgentStepTaskId(message: WsConsoleMessage): string | null {
  return message.task_id || null;
}

function normalizeStatusErrorType(message: WsConsoleMessage): string | undefined {
  return message.error_type || message.data?.error_type;
}

function normalizeStatusReason(message: WsConsoleMessage): string | undefined {
  return message.data?.reason;
}

function normalizeStatusFinalReasoning(message: WsConsoleMessage): string | undefined {
  return message.data?.final_reasoning;
}

function statusShouldStopThinking(status?: string): boolean {
  return status === 'completed' || status === 'failed' || status === 'interrupted';
}

function resetTransientProgressState() {
  return {
    currentPhase: null as 'reason' | 'act' | 'observe' | null,
    isThinking: false,
    thinkingContent: '',
  };
}

function buildProgressBaseMessage(message: WsConsoleMessage, progressKey: string): ChatMessage {
  return {
    id: buildProgressMessageId(progressKey),
    role: 'agent',
    content: buildProgressBubbleContent(message),
    timestamp: normalizeProgressTimestampValue(),
    taskId: normalizeProgressTaskId(message) || undefined,
    stepNumber: normalizeProgressStepNumber(message),
    progressKey,
    progressPhase: normalizeProgressPhase(message),
    progressStage: normalizeProgressStage(message),
    progressMessage: normalizeProgressMessageText(message),
    progressStatusText: normalizeProgressStatus(message),
    thinking: normalizeProgressThinking(message),
    action: normalizeProgressAction(message),
    screenshot: normalizeProgressScreenshot(message),
    result: normalizeProgressResult(message),
    success: normalizeProgressSuccess(message),
    error: normalizeProgressError(message),
    errorType: normalizeProgressErrorType(message),
    isProgressMessage: true,
    isCompleted: shouldFinalizeProgressMessage(message),
  };
}

function updateProgressMessageFromWs(existing: ChatMessage, message: WsConsoleMessage): ChatMessage {
  return mergeProgressMessage(existing, {
    content: buildProgressBubbleContent(message),
    timestamp: normalizeProgressTimestampValue(),
    progressPhase: normalizeProgressPhase(message),
    progressStage: normalizeProgressStage(message),
    progressMessage: normalizeProgressMessageText(message),
    progressStatusText: normalizeProgressStatus(message),
    thinking: normalizeProgressThinking(message),
    action: normalizeProgressAction(message),
    screenshot: normalizeProgressScreenshot(message),
    result: normalizeProgressResult(message),
    success: normalizeProgressSuccess(message),
    error: normalizeProgressError(message),
    errorType: normalizeProgressErrorType(message),
    isProgressMessage: true,
    isCompleted: shouldFinalizeProgressMessage(message),
  });
}

function upsertProgressIntoHistory(
  history: ChatMessage[],
  progressKey: string,
  message: WsConsoleMessage,
): ChatMessage[] {
  const base = buildProgressBaseMessage(message, progressKey);
  if (!hasProgressMessage(history, progressKey)) {
    return [...history, base];
  }

  return updateProgressHistoryByKey(history, progressKey, (existing) => updateProgressMessageFromWs(existing, message));
}

function markStepProgressCompleted(
  history: ChatMessage[],
  progressKey: string,
  message: WsConsoleMessage,
): ChatMessage[] {
  return finalizeProgressForStep(history, progressKey, {
    content: normalizeAgentStepContent(message),
    timestamp: normalizeAgentStepTimestamp(),
    thinking: normalizeAgentStepThinking(message),
    action: normalizeAgentStepAction(message),
    screenshot: normalizeAgentStepScreenshot(message),
    result: normalizeAgentStepResult(message),
    success: normalizeAgentStepSuccess(message),
    error: normalizeAgentStepError(message),
    errorType: normalizeAgentStepErrorType(message),
    progressStage: message.success === false ? 'observe_timeout' : 'observe_received',
    progressStatusText: message.success === false ? '已失败' : '已完成',
  });
}

function buildStandaloneAgentStepMessage(message: WsConsoleMessage): ChatMessage {
  return {
    id: generateId(),
    role: 'agent',
    content: normalizeAgentStepContent(message),
    timestamp: normalizeAgentStepTimestamp(),
    taskId: normalizeAgentStepTaskId(message) || undefined,
    stepNumber: normalizeAgentStepNumber(message),
    thinking: normalizeAgentStepThinking(message),
    action: normalizeAgentStepAction(message),
    screenshot: normalizeAgentStepScreenshot(message),
    result: normalizeAgentStepResult(message),
    success: normalizeAgentStepSuccess(message),
    error: normalizeAgentStepError(message),
    errorType: normalizeAgentStepErrorType(message),
    isCompleted: true,
  };
}

function applyStatusToState(
  history: ChatMessage[],
  message: WsConsoleMessage,
): ChatMessage[] {
  const taskId = normalizeStatusTaskId(message);

  if (message.status === 'finished' || message.status === 'completed') {
    return progressHistoryAfterStatus(history, taskId, 'completed', normalizeCompletionMessage(message));
  }
  if (message.status === 'error' || message.status === 'failed') {
    return progressHistoryAfterStatus(
      history,
      taskId,
      'failed',
      normalizeFailureMessage(message),
      normalizeStatusErrorType(message),
    );
  }
  if (message.status === 'interrupted') {
    return progressHistoryAfterStatus(history, taskId, 'interrupted', normalizeInterruptionMessage(message));
  }

  return history;
}

function updateProgressMapForStatus(
  progressMessageIds: Record<string, string>,
  message: WsConsoleMessage,
): Record<string, string> {
  if (message.status === 'finished' || message.status === 'completed' || message.status === 'error' || message.status === 'failed' || message.status === 'interrupted') {
    return removeTaskProgressTracking(progressMessageIds, normalizeStatusTaskId(message));
  }
  return progressMessageIds;
}

function updateStoreTransientForProgress(message: WsConsoleMessage) {
  return {
    currentPhase: normalizeProgressPhase(message),
    isThinking: normalizeProgressPhase(message) === 'reason' && !shouldHideThinkingPanel(message),
    thinkingContent: normalizeProgressThinking(message) || '',
  };
}

function updateStoreTransientForStep() {
  return resetTransientProgressState();
}

function updateStoreTransientForStatus(message: WsConsoleMessage) {
  return statusShouldStopThinking(message.status) ? resetTransientProgressState() : {};
}

function shouldUpdateScreenshotFromMessage(message: WsConsoleMessage): boolean {
  return Boolean(message.screenshot);
}

function shouldUpdateDeviceBusyStatus(_message: WsConsoleMessage): boolean {
  return false;
}

function isTerminalAgentStatus(status?: string): boolean {
  return status === 'finished' || status === 'completed' || status === 'error' || status === 'failed' || status === 'interrupted';
}

function shouldForgetMessagesAfterStatus(status?: string): boolean {
  return status === 'finished' || status === 'completed';
}

function progressMessageIdForState(progressMessageIds: Record<string, string>, progressKey: string): string {
  return progressMessageIds[progressKey] || buildProgressMessageId(progressKey);
}

function upsertProgressStateHistory(
  history: ChatMessage[],
  progressKey: string,
  messageId: string,
  message: WsConsoleMessage,
): ChatMessage[] {
  const baseMessage = buildProgressBaseMessage(message, progressKey);
  baseMessage.id = messageId;
  return upsertConversationProgress(history, messageId, baseMessage);
}

function updateProgressStateHistory(
  history: ChatMessage[],
  progressKey: string,
  messageId: string,
  message: WsConsoleMessage,
): ChatMessage[] {
  return hasProgressMessage(history, progressKey)
    ? updateProgressHistoryByKey(history, progressKey, (existing) => updateProgressMessageFromWs(existing, message))
    : upsertProgressStateHistory(history, progressKey, messageId, message);
}

function progressMessageExists(history: ChatMessage[], progressKey: string): boolean {
  return hasProgressMessage(history, progressKey);
}

function nextProgressMessageId(progressMessageIds: Record<string, string>, progressKey: string): string {
  return progressMessageIdForState(progressMessageIds, progressKey);
}

function createProgressMessageId(progressKey: string): string {
  return buildProgressMessageId(progressKey);
}

function shouldAppendStandaloneStatusMessage(history: ChatMessage[], taskId: string | null): boolean {
  return !history.some((msg) => msg.taskId === taskId && msg.isProgressMessage && !msg.isCompleted);
}

function clearTransientProgressOnStatus(status?: string) {
  return statusShouldStopThinking(status) ? resetTransientProgressState() : {};
}

function normalizeStatusContent(message: WsConsoleMessage): string {
  if (message.status === 'finished' || message.status === 'completed') return normalizeCompletionMessage(message);
  if (message.status === 'error' || message.status === 'failed') return normalizeFailureMessage(message);
  if (message.status === 'interrupted') return normalizeInterruptionMessage(message);
  return message.message || '';
}

function buildStandaloneStatusMessage(message: WsConsoleMessage): ChatMessage {
  return {
    id: generateId(),
    role: 'agent',
    content: normalizeStatusContent(message),
    timestamp: new Date().toISOString(),
    taskId: normalizeStatusTaskId(message) || undefined,
    errorType: normalizeStatusErrorType(message),
    success: message.status === 'completed' || message.status === 'finished',
    error: message.status === 'failed' || message.status === 'error' || message.status === 'interrupted'
      ? normalizeStatusContent(message)
      : undefined,
    isCompleted: true,
  };
}

function ensureStatusMessage(history: ChatMessage[], message: WsConsoleMessage): ChatMessage[] {
  const taskId = normalizeStatusTaskId(message);
  if (!shouldAppendStandaloneStatusMessage(history, taskId)) {
    return history;
  }
  return [...history, buildStandaloneStatusMessage(message)];
}

function isFailureStatus(status?: string): boolean {
  return status === 'error' || status === 'failed';
}

function isCompletionStatus(status?: string): boolean {
  return status === 'finished' || status === 'completed';
}

function isInterruptionStatus(status?: string): boolean {
  return status === 'interrupted';
}

function normalizeFailureStateMessage(message: WsConsoleMessage): string {
  return normalizeFailureMessage(message);
}

function normalizeCompletionStateMessage(message: WsConsoleMessage): string {
  return normalizeCompletionMessage(message);
}

function normalizeInterruptionStateMessage(message: WsConsoleMessage): string {
  return normalizeInterruptionMessage(message);
}

function shouldKeepStatusOnlyMessage(message: WsConsoleMessage): boolean {
  return isTerminalAgentStatus(message.status);
}

function progressTrackingFromState(state: { progressMessageIds: Record<string, string> }) {
  return state.progressMessageIds;
}

function withUpdatedProgressMessage(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
): { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string>; messageId: string | null } {
  const progressKey = normalizeProgressKey(message);
  if (!progressKey) {
    return {
      conversationHistory: state.conversationHistory,
      progressMessageIds: state.progressMessageIds,
      messageId: null,
    };
  }

  const messageId = nextProgressMessageId(state.progressMessageIds, progressKey);
  const conversationHistory = updateProgressStateHistory(state.conversationHistory, progressKey, messageId, message);
  const progressMessageIds = updateProgressTracking(state.progressMessageIds, progressKey, messageId);
  return { conversationHistory, progressMessageIds, messageId };
}

function finalizeStepInState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
): { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> } {
  const progressKey = normalizeAgentStepProgressKey(message);
  if (!progressKey || !progressMessageExists(state.conversationHistory, progressKey)) {
    return state;
  }

  return {
    conversationHistory: markStepProgressCompleted(state.conversationHistory, progressKey, message),
    progressMessageIds: removeProgressTracking(state.progressMessageIds, progressKey),
  };
}

function appendStandaloneStepInState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
): { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> } {
  return {
    conversationHistory: [...state.conversationHistory, buildStandaloneAgentStepMessage(message)],
    progressMessageIds: state.progressMessageIds,
  };
}

function applyStatusInState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
): { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> } {
  const conversationHistory = ensureStatusMessage(applyStatusToState(state.conversationHistory, message), message);
  const progressMessageIds = updateProgressMapForStatus(state.progressMessageIds, message);
  return { conversationHistory, progressMessageIds };
}

function shouldShowStepAsSeparateBubble(state: { conversationHistory: ChatMessage[] }, message: WsConsoleMessage): boolean {
  return !shouldSuppressStandaloneAgentStep(state.conversationHistory, normalizeAgentStepProgressKey(message));
}

function shouldTrackProgressMessage(message: WsConsoleMessage): boolean {
  return Boolean(normalizeProgressKey(message));
}

function shouldUpdateCurrentPhaseFromProgress(_message: WsConsoleMessage): boolean {
  return true;
}

function shouldResetPhaseOnStep(): boolean {
  return true;
}

function shouldResetPhaseOnStatus(status?: string): boolean {
  return statusShouldStopThinking(status);
}

function getDeviceBusyPatch() {
  return { status: 'busy' as const };
}

function getDeviceIdlePatch(currentDeviceStatus?: string) {
  return { status: currentDeviceStatus === 'offline' ? 'offline' as const : 'idle' as const };
}

function getDeviceErrorPatch() {
  return { status: 'error' as const };
}

function setCurrentStepFromAgentStep(message: WsConsoleMessage): AgentStep {
  return {
    id: `step_${normalizeAgentStepNumber(message)}`,
    phase: 'action',
    action: {
      type: (message.action?.action || 'unknown') as any,
      params: message.action || {},
      description: formatActionDescription(message.action),
    },
    thinking: message.reasoning || '',
    timestamp: new Date().toISOString(),
    success: message.success ?? true,
    step_number: normalizeAgentStepNumber(message),
    error: message.error,
  };
}

function updateProgressMessageWithStep(message: WsConsoleMessage): Partial<ChatMessage> {
  return {
    content: normalizeAgentStepContent(message),
    thinking: normalizeAgentStepThinking(message),
    action: normalizeAgentStepAction(message),
    screenshot: normalizeAgentStepScreenshot(message),
    result: normalizeAgentStepResult(message),
    success: normalizeAgentStepSuccess(message),
    error: normalizeAgentStepError(message),
    errorType: normalizeAgentStepErrorType(message),
    progressStage: message.success === false ? 'observe_timeout' : 'observe_received',
    progressStatusText: message.success === false ? '已失败' : '已完成',
    isCompleted: true,
  };
}

function shouldUseExistingProgressMessage(state: { conversationHistory: ChatMessage[] }, message: WsConsoleMessage): boolean {
  return hasAnyStepProgressMessage(state, message);
}

function removeStepProgressTracking(
  progressMessageIds: Record<string, string>,
  message: WsConsoleMessage,
): Record<string, string> {
  return removeAllStepProgressTracking(progressMessageIds, message);
}

function getTaskIdOrUndefined(taskId: string | null): string | undefined {
  return taskId || undefined;
}

function getReasoningOrUndefined(reasoning?: string): string | undefined {
  return reasoning || undefined;
}

function getScreenshotOrUndefined(screenshot?: string): string | undefined {
  return screenshot || undefined;
}

function getErrorOrUndefined(error?: string): string | undefined {
  return error || undefined;
}

function getResultOrUndefined(result?: string): string | undefined {
  return result || undefined;
}

function buildAgentStepChatMessage(message: WsConsoleMessage): ChatMessage {
  return {
    id: generateId(),
    role: 'agent',
    content: normalizeAgentStepContent(message),
    timestamp: normalizeAgentStepTimestamp(),
    taskId: getTaskIdOrUndefined(normalizeAgentStepTaskId(message)),
    stepNumber: normalizeAgentStepNumber(message),
    thinking: getReasoningOrUndefined(message.reasoning),
    action: normalizeAgentStepAction(message),
    screenshot: getScreenshotOrUndefined(message.screenshot),
    result: getResultOrUndefined(message.result),
    success: normalizeAgentStepSuccess(message),
    error: getErrorOrUndefined(message.error),
    errorType: normalizeAgentStepErrorType(message),
    isCompleted: true,
  };
}

function buildProgressChatMessage(message: WsConsoleMessage, progressKey: string, messageId: string): ChatMessage {
  const chatMessage = buildProgressBaseMessage(message, progressKey);
  chatMessage.id = messageId;
  return chatMessage;
}

function applyProgressMessageToHistory(
  history: ChatMessage[],
  message: WsConsoleMessage,
  progressKey: string,
  messageId: string,
): ChatMessage[] {
  const chatMessage = buildProgressChatMessage(message, progressKey, messageId);
  return upsertConversationProgress(history, messageId, chatMessage);
}

function progressMessageMapAfterUpsert(
  progressMessageIds: Record<string, string>,
  progressKey: string,
  messageId: string,
): Record<string, string> {
  return updateProgressTracking(progressMessageIds, progressKey, messageId);
}

function getOrCreateProgressMessageId(
  progressMessageIds: Record<string, string>,
  progressKey: string,
): string {
  return progressMessageIds[progressKey] || buildProgressMessageId(progressKey);
}

function isReasonPhase(message: WsConsoleMessage): boolean {
  return normalizeProgressPhase(message) === 'reason';
}

function getThinkingPanelStateFromProgress(message: WsConsoleMessage) {
  return {
    currentPhase: normalizeProgressPhase(message),
    isThinking: isReasonPhase(message) && !shouldHideThinkingPanel(message),
    thinkingContent: normalizeProgressThinking(message) || '',
  };
}

function getThinkingPanelStateFromStep() {
  return resetTransientProgressState();
}

function getThinkingPanelStateFromStatus(message: WsConsoleMessage) {
  return clearTransientProgressOnStatus(message.status);
}

function buildProgressUpsertResult(
  conversationHistory: ChatMessage[],
  progressMessageIds: Record<string, string>,
  messageId: string | null,
) {
  return { conversationHistory, progressMessageIds, messageId };
}

function buildNoProgressUpsertResult(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
) {
  return {
    conversationHistory: state.conversationHistory,
    progressMessageIds: state.progressMessageIds,
    messageId: null,
  };
}

function withProgressMessage(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  const progressKey = normalizeProgressKey(message);
  if (!progressKey) {
    return buildNoProgressUpsertResult(state);
  }
  const messageId = getOrCreateProgressMessageId(state.progressMessageIds, progressKey);
  return buildProgressUpsertResult(
    applyProgressMessageToHistory(state.conversationHistory, message, progressKey, messageId),
    progressMessageMapAfterUpsert(state.progressMessageIds, progressKey, messageId),
    messageId,
  );
}

function getProgressKeyFromTaskStep(taskId?: string | null, stepNumber?: number): string | null {
  return getProgressKey(taskId || undefined, stepNumber);
}

function clearProgressOnSessionReset() {
  return {} as Record<string, string>;
}

function mergeSavedHistory(messages: ChatMessage[]): ChatMessage[] {
  return messages;
}

function normalizeInitialProgressMap(): Record<string, string> {
  return {};
}

function normalizeSavedHistory(messages: ChatMessage[]): ChatMessage[] {
  return mergeSavedHistory(messages);
}

function shouldUpdateBusyStatusFromProgress(): boolean {
  return true;
}

function shouldUpdateBusyStatusFromStep(): boolean {
  return true;
}

function normalizeStatusRunning(message: WsConsoleMessage): boolean {
  return hasActiveBackendTask(normalizeStatusTaskId(message), message.status, true);
}

function normalizeStatusPending(message: WsConsoleMessage, currentTaskId: string | null, canInterrupt: boolean): boolean {
  return hasActiveBackendTask(currentTaskId, message.status, canInterrupt);
}

function normalizeTaskIdForStatus(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return normalizeStatusTaskId(message) || currentTaskId;
}

function normalizeTaskIdForProgress(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return normalizeProgressTaskId(message) || currentTaskId;
}

function normalizeTaskIdForStep(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return normalizeAgentStepTaskId(message) || currentTaskId;
}

function updateCurrentTaskIdFromMessage(taskIdFromMessage: string | null, currentTaskId: string | null): string | null {
  return taskIdFromMessage || currentTaskId;
}

function shouldSetCurrentTaskIdFromProgress(message: WsConsoleMessage): boolean {
  return Boolean(normalizeProgressTaskId(message));
}

function shouldSetCurrentTaskIdFromStep(message: WsConsoleMessage): boolean {
  return Boolean(normalizeAgentStepTaskId(message));
}

function shouldSetCurrentTaskIdFromStatus(message: WsConsoleMessage): boolean {
  return Boolean(normalizeStatusTaskId(message));
}

function normalizeMessageTaskId(message: WsConsoleMessage): string | null {
  return message.task_id || message.data?.task_id || null;
}

function normalizeMessageErrorType(message: WsConsoleMessage): string | undefined {
  return message.error_type || message.data?.error_type;
}

function normalizeMessageSuccess(message: WsConsoleMessage): boolean | undefined {
  return message.success;
}

function normalizeMessageScreenshot(message: WsConsoleMessage): string | undefined {
  return message.screenshot || undefined;
}

function normalizeMessageReasoning(message: WsConsoleMessage): string | undefined {
  return message.reasoning || undefined;
}

function normalizeMessageAction(message: WsConsoleMessage): AgentAction | undefined {
  return normalizeAction(message.action);
}

function normalizeMessageResult(message: WsConsoleMessage): string | undefined {
  return message.result || undefined;
}

function normalizeMessageError(message: WsConsoleMessage): string | undefined {
  return message.error || undefined;
}

function normalizeProgressMessageUpdates(message: WsConsoleMessage): Partial<ChatMessage> {
  return {
    content: buildProgressBubbleContent(message),
    timestamp: normalizeProgressTimestampValue(),
    taskId: normalizeProgressTaskId(message) || undefined,
    stepNumber: normalizeProgressStepNumber(message),
    progressPhase: normalizeProgressPhase(message),
    progressStage: normalizeProgressStage(message),
    progressMessage: normalizeProgressMessageText(message),
    progressStatusText: normalizeProgressStatus(message),
    thinking: normalizeProgressThinking(message),
    action: normalizeProgressAction(message),
    screenshot: normalizeProgressScreenshot(message),
    result: normalizeProgressResult(message),
    success: normalizeProgressSuccess(message),
    error: normalizeProgressError(message),
    errorType: normalizeProgressErrorType(message),
    isProgressMessage: true,
    isCompleted: shouldFinalizeProgressMessage(message),
  };
}

function updateExistingProgressMessage(msg: ChatMessage, message: WsConsoleMessage): ChatMessage {
  return mergeProgressMessage(msg, normalizeProgressMessageUpdates(message));
}

function upsertProgressMessageInHistory(history: ChatMessage[], message: WsConsoleMessage, progressKey: string, messageId: string): ChatMessage[] {
  const exists = hasProgressMessage(history, progressKey);
  if (!exists) {
    return [...history, { ...buildProgressBaseMessage(message, progressKey), id: messageId }];
  }
  return updateProgressHistoryByKey(history, progressKey, (msg) => updateExistingProgressMessage(msg, message));
}

function finalizeProgressWithStep(history: ChatMessage[], message: WsConsoleMessage): ChatMessage[] {
  const progressKey = normalizeAgentStepProgressKey(message);
  if (!progressKey) return history;
  return finalizeProgressForStep(history, progressKey, updateProgressMessageWithStep(message));
}

function buildStandaloneFailureMessage(content: string): ChatMessage {
  return {
    id: generateId(),
    role: 'agent',
    content,
    timestamp: new Date().toISOString(),
    isCompleted: true,
    error: content,
  };
}

function shouldAppendStandaloneFailure(history: ChatMessage[], taskId: string | null): boolean {
  return !history.some((msg) => msg.taskId === taskId && msg.isProgressMessage);
}

function appendStandaloneFailure(history: ChatMessage[], taskId: string | null, content: string): ChatMessage[] {
  if (!shouldAppendStandaloneFailure(history, taskId)) {
    return history;
  }
  return [...history, { ...buildStandaloneFailureMessage(content), taskId: taskId || undefined }];
}

function normalizeAgentStatusMessage(message: WsConsoleMessage): string {
  return normalizeStatusContent(message);
}

function normalizeAgentStatusErrorType(message: WsConsoleMessage): string | undefined {
  return normalizeStatusErrorType(message);
}

function normalizeAgentStatusTaskId(message: WsConsoleMessage): string | null {
  return normalizeStatusTaskId(message);
}

function isRunningStatus(status?: string): boolean {
  return status === 'running';
}

function isPendingStatus(status?: string): boolean {
  return status === 'pending';
}

function resetProgressMap(): Record<string, string> {
  return {};
}

function normalizeConversationHistory(messages: ChatMessage[]): ChatMessage[] {
  return messages;
}

function normalizeSnapshotConversation(messages: ChatMessage[]): ChatMessage[] {
  return normalizeConversationHistory(messages);
}

function normalizeTaskCreatedState() {
  return {
    currentStepNum: 0,
    history: [],
    currentStep: null,
    currentScreenshot: null,
    currentApp: '未知',
    canInterrupt: true,
    canResume: false,
    progressMessageIds: resetProgressMap(),
    ...resetTransientProgressState(),
  };
}

function normalizeEndSessionState() {
  return {
    currentTaskId: null,
    isRunning: false,
    isLocked: false,
    controllerId: null,
    currentStep: null,
    pendingAction: null,
    status: 'pending' as const,
    history: [],
    conversationHistory: [],
    currentScreenshot: null,
    currentApp: '未知',
    canInterrupt: false,
    canResume: false,
    progressMessageIds: resetProgressMap(),
    ...resetTransientProgressState(),
  };
}

function normalizeInitSessionState(savedHistory: ChatMessage[]) {
  const conversationHistory = normalizeSnapshotConversation(savedHistory);
  return {
    conversationHistory,
    progressMessageIds: progressTrackingFromConversation(conversationHistory),
    ...transientStateFromConversation(conversationHistory),
  };
}

function normalizeInitErrorState() {
  return {
    conversationHistory: [],
    progressMessageIds: resetProgressMap(),
    ...resetTransientProgressState(),
  };
}

function normalizeSendCommandStartState() {
  return {
    pendingAction: null,
    waitingForConfirm: false,
    waitingConfirmPhase: null,
    progressMessageIds: resetProgressMap(),
    ...resetTransientProgressState(),
  };
}

function normalizeInterruptState() {
  return {
    isRunning: false,
    pendingAction: null,
    status: 'interrupted' as const,
    canInterrupt: false,
    canResume: false,
  };
}

function normalizeProgressBaseState(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    currentTaskId: updateCurrentTaskIdFromMessage(normalizeTaskIdForProgress(message, currentTaskId), currentTaskId),
    currentStepNum: normalizeProgressStepNumber(message) || 0,
    ...getThinkingPanelStateFromProgress(message),
  };
}

function normalizeStepBaseState(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    currentTaskId: updateCurrentTaskIdFromMessage(normalizeTaskIdForStep(message, currentTaskId), currentTaskId),
    currentStepNum: normalizeAgentStepNumber(message),
    ...getThinkingPanelStateFromStep(),
  };
}

function normalizeStatusBaseState(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    currentTaskId: updateCurrentTaskIdFromMessage(normalizeTaskIdForStatus(message, currentTaskId), currentTaskId),
    ...getThinkingPanelStateFromStatus(message),
  };
}

function normalizeStatusRunningState(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    isRunning: normalizeStatusRunning(message),
    status: 'running' as const,
    currentTaskId: updateCurrentTaskIdFromMessage(normalizeTaskIdForStatus(message, currentTaskId), currentTaskId),
    canInterrupt: Boolean(normalizeStatusTaskId(message) || currentTaskId),
    canResume: false,
  };
}

function normalizeStatusPendingState(message: WsConsoleMessage, currentTaskId: string | null, canInterrupt: boolean) {
  return {
    isRunning: normalizeStatusPending(message, currentTaskId, canInterrupt),
    status: 'pending' as const,
    currentTaskId: updateCurrentTaskIdFromMessage(normalizeTaskIdForStatus(message, currentTaskId), currentTaskId),
    canInterrupt: Boolean(normalizeStatusTaskId(message) || currentTaskId),
    canResume: false,
  };
}

function normalizeStatusCompletedState(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    isRunning: false,
    status: 'completed' as const,
    currentTaskId: updateCurrentTaskIdFromMessage(normalizeTaskIdForStatus(message, currentTaskId), currentTaskId),
    canInterrupt: false,
    canResume: false,
  };
}

function normalizeStatusFailedState(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    isRunning: false,
    status: 'failed' as const,
    error: message.message || 'Task failed',
    currentTaskId: updateCurrentTaskIdFromMessage(normalizeTaskIdForStatus(message, currentTaskId), currentTaskId),
    canInterrupt: false,
    canResume: false,
  };
}

function normalizeStatusInterruptedState(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    isRunning: false,
    status: 'interrupted' as const,
    currentTaskId: updateCurrentTaskIdFromMessage(normalizeTaskIdForStatus(message, currentTaskId), currentTaskId),
    canInterrupt: false,
    canResume: false,
  };
}

function shouldStatusUpdateDeviceIdle(status?: string): boolean {
  return isCompletionStatus(status) || isInterruptionStatus(status);
}

function shouldStatusUpdateDeviceError(status?: string): boolean {
  return isFailureStatus(status);
}

function shouldStatusUpdateDeviceBusy(status?: string): boolean {
  return isRunningStatus(status) || isPendingStatus(status);
}

function getConversationHistoryFromStatus(state: { conversationHistory: ChatMessage[] }, message: WsConsoleMessage): ChatMessage[] {
  return applyStatusToState(state.conversationHistory, message);
}

function withStandaloneStatusMessage(history: ChatMessage[], message: WsConsoleMessage): ChatMessage[] {
  return ensureStatusMessage(history, message);
}

function withStatusConversationHistory(state: { conversationHistory: ChatMessage[] }, message: WsConsoleMessage): ChatMessage[] {
  return withStandaloneStatusMessage(getConversationHistoryFromStatus(state, message), message);
}

function statusProgressMapAfterMessage(progressMessageIds: Record<string, string>, message: WsConsoleMessage): Record<string, string> {
  return updateProgressMapForStatus(progressMessageIds, message);
}

function normalizeProgressKeyValue(message: WsConsoleMessage): string | null {
  return normalizeProgressKey(message);
}


function normalizeStepProgressKeyValue(message: WsConsoleMessage): string | null {
  return normalizeAgentStepProgressKey(message);
}

function normalizeMessageIdForProgress(progressMessageIds: Record<string, string>, message: WsConsoleMessage): string | null {
  const progressKey = normalizeProgressKeyValue(message);
  if (!progressKey) return null;
  return getOrCreateProgressMessageId(progressMessageIds, progressKey);
}

function normalizeTaskFinishedState(message: WsConsoleMessage) {
  return message.status === 'finished' || message.status === 'completed';
}

function normalizeTaskFailedState(message: WsConsoleMessage) {
  return message.status === 'error' || message.status === 'failed';
}

function normalizeTaskInterruptedState(message: WsConsoleMessage) {
  return message.status === 'interrupted';
}

function normalizeCurrentDeviceStatusForInterrupt(deviceId: string | null): string | undefined {
  return deviceId ? useDeviceStore.getState().getDeviceById(deviceId)?.status : undefined;
}

function normalizeFailureDisplayMessage(message: WsConsoleMessage): string {
  return normalizeFailureMessage(message);
}

function normalizeCompletionDisplayMessage(message: WsConsoleMessage): string {
  return normalizeCompletionMessage(message);
}

function normalizeInterruptDisplayMessage(message: WsConsoleMessage): string {
  return normalizeInterruptionMessage(message);
}

function shouldClearProgressAfterStatus(message: WsConsoleMessage): boolean {
  return isTerminalAgentStatus(message.status);
}

function shouldTrackTaskCreatedProgressReset(): boolean {
  return true;
}

function buildProgressConversationEntry(message: WsConsoleMessage, progressKey: string, messageId: string): ChatMessage {
  return { ...buildProgressBaseMessage(message, progressKey), id: messageId };
}

function buildProgressConversationUpdates(message: WsConsoleMessage): Partial<ChatMessage> {
  return normalizeProgressMessageUpdates(message);
}

function mergeProgressConversationMessage(existing: ChatMessage, message: WsConsoleMessage): ChatMessage {
  return mergeProgressMessage(existing, buildProgressConversationUpdates(message));
}

function addOrUpdateProgressConversationEntry(history: ChatMessage[], message: WsConsoleMessage, progressKey: string, messageId: string): ChatMessage[] {
  if (!hasProgressMessage(history, progressKey)) {
    return [...history, buildProgressConversationEntry(message, progressKey, messageId)];
  }
  return updateProgressHistoryByKey(history, progressKey, (existing) => mergeProgressConversationMessage(existing, message));
}

function normalizeProgressConversationState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  const progressKey = normalizeProgressKeyValue(message);
  if (!progressKey) {
    return buildNoProgressUpsertResult(state);
  }
  const messageId = getOrCreateProgressMessageId(state.progressMessageIds, progressKey);
  return buildProgressUpsertResult(
    addOrUpdateProgressConversationEntry(state.conversationHistory, message, progressKey, messageId),
    progressMessageMapAfterUpsert(state.progressMessageIds, progressKey, messageId),
    messageId,
  );
}

function normalizeAgentStepConversationState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  const usedProgressMessage = shouldUseExistingProgressMessage(state, message);
  const nextHistory = usedProgressMessage
    ? markAllStepProgressCompleted(state.conversationHistory, message)
    : [...state.conversationHistory, buildStandaloneAgentStepMessage(message)];

  return {
    conversationHistory: nextHistory,
    progressMessageIds: removeStepProgressTracking(state.progressMessageIds, message),
  };
}

function normalizeAgentStatusConversationState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return applyStatusInState(state, message);
}

function normalizeProgressCurrentTaskId(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return updateCurrentTaskIdFromMessage(normalizeProgressTaskId(message), currentTaskId);
}

function normalizeAgentStepCurrentTaskId(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return updateCurrentTaskIdFromMessage(normalizeAgentStepTaskId(message), currentTaskId);
}

function normalizeAgentStatusCurrentTaskId(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return updateCurrentTaskIdFromMessage(normalizeStatusTaskId(message), currentTaskId);
}

function normalizeMessageContentForFailure(message: WsConsoleMessage): string {
  return normalizeFailureDisplayMessage(message);
}

function normalizeMessageContentForCompletion(message: WsConsoleMessage): string {
  return normalizeCompletionDisplayMessage(message);
}

function normalizeMessageContentForInterrupt(message: WsConsoleMessage): string {
  return normalizeInterruptDisplayMessage(message);
}

function normalizeCurrentApp(app: string | undefined): string {
  return app || '未知';
}

function normalizeProgressMessageId(progressKey: string): string {
  return buildProgressMessageId(progressKey);
}

function normalizeProgressMapAfterStep(progressMessageIds: Record<string, string>, message: WsConsoleMessage): Record<string, string> {
  return removeStepProgressTracking(progressMessageIds, message);
}

function normalizeProgressMapAfterStatus(progressMessageIds: Record<string, string>, message: WsConsoleMessage): Record<string, string> {
  return statusProgressMapAfterMessage(progressMessageIds, message);
}

function normalizeProgressMapAfterReset(): Record<string, string> {
  return resetProgressMap();
}

function shouldUseProgressState(message: WsConsoleMessage): boolean {
  return shouldTrackProgressMessage(message);
}

function shouldUseAgentStepState(_message: WsConsoleMessage): boolean {
  return true;
}

function shouldUseAgentStatusState(_message: WsConsoleMessage): boolean {
  return true;
}

function buildDeviceBusyStatePatch() {
  return getDeviceBusyPatch();
}

function buildDeviceIdleStatePatch(currentDeviceStatus?: string) {
  return getDeviceIdlePatch(currentDeviceStatus);
}

function buildDeviceErrorStatePatch() {
  return getDeviceErrorPatch();
}

function shouldSetScreenshot(message: WsConsoleMessage): boolean {
  return shouldUpdateScreenshotFromMessage(message);
}

function normalizeStepAsCurrent(message: WsConsoleMessage): AgentStep {
  return setCurrentStepFromAgentStep(message);
}

function normalizeProgressUpsertState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeProgressConversationState(state, message);
}

function normalizeStepConversationState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeAgentStepConversationState(state, message);
}

function normalizeStatusConversationState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeAgentStatusConversationState(state, message);
}

function normalizeProgressTransientState(message: WsConsoleMessage) {
  return normalizeProgressBaseState(message, null);
}

function normalizeStepTransientState(message: WsConsoleMessage) {
  return normalizeStepBaseState(message, null);
}

function normalizeStatusTransientState(message: WsConsoleMessage) {
  return normalizeStatusBaseState(message, null);
}

function normalizeSavedConversation(messages: ChatMessage[]): ChatMessage[] {
  return normalizeSavedHistory(messages);
}

function normalizeProgressStateReset() {
  return resetProgressMap();
}

function normalizeTaskCreatedProgressState() {
  return resetProgressMap();
}

function normalizeStatusStateReset() {
  return resetTransientProgressState();
}

function normalizeProgressPanelState(message: WsConsoleMessage) {
  return getThinkingPanelStateFromProgress(message);
}

function normalizeStepPanelState() {
  return getThinkingPanelStateFromStep();
}

function normalizeStatusPanelState(message: WsConsoleMessage) {
  return getThinkingPanelStateFromStatus(message);
}

function normalizeConversationWithStatus(state: { conversationHistory: ChatMessage[] }, message: WsConsoleMessage): ChatMessage[] {
  return withStatusConversationHistory(state, message);
}

function normalizeConversationWithProgress(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeProgressUpsertState(state, message);
}

function normalizeConversationWithStep(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeStepConversationState(state, message);
}

function normalizeCurrentStep(message: WsConsoleMessage): AgentStep {
  return normalizeStepAsCurrent(message);
}

function normalizeConversationCompletion(history: ChatMessage[], message: WsConsoleMessage): ChatMessage[] {
  return applyStatusToState(history, message);
}

function normalizeProgressRecordKey(message: WsConsoleMessage): string | null {
  return normalizeProgressKeyValue(message);
}

function normalizeProgressRecordId(progressMessageIds: Record<string, string>, message: WsConsoleMessage): string | null {
  return normalizeMessageIdForProgress(progressMessageIds, message);
}

function normalizeProgressConversationUpdate(message: WsConsoleMessage): Partial<ChatMessage> {
  return buildProgressConversationUpdates(message);
}

function normalizeProgressConversationEntry(message: WsConsoleMessage, progressKey: string, messageId: string): ChatMessage {
  return buildProgressConversationEntry(message, progressKey, messageId);
}

function normalizeStatusResultMessage(message: WsConsoleMessage): string {
  return normalizeStatusContent(message);
}

function normalizeAgentProgressMessage(message: WsConsoleMessage): string {
  return buildProgressBubbleContent(message);
}

function normalizeAgentProgressStatus(message: WsConsoleMessage): string {
  return normalizeProgressStatus(message);
}

function normalizeAgentProgressPhase(message: WsConsoleMessage): 'reason' | 'act' | 'observe' {
  return normalizeProgressPhase(message);
}

function normalizeAgentProgressStage(message: WsConsoleMessage): string {
  return normalizeProgressStage(message);
}

function normalizeAgentProgressTaskId(message: WsConsoleMessage): string | null {
  return normalizeProgressTaskId(message);
}

function normalizeAgentProgressStep(message: WsConsoleMessage): number {
  return normalizeProgressStepNumber(message);
}

function normalizeAgentProgressErrorType(message: WsConsoleMessage): string | undefined {
  return normalizeProgressErrorType(message);
}

function normalizeAgentProgressError(message: WsConsoleMessage): string | undefined {
  return normalizeProgressError(message);
}

function normalizeAgentProgressSuccess(message: WsConsoleMessage): boolean | undefined {
  return normalizeProgressSuccess(message);
}

function normalizeAgentProgressThinking(message: WsConsoleMessage): string | undefined {
  return normalizeProgressThinking(message);
}

function normalizeAgentProgressAction(message: WsConsoleMessage): AgentAction | undefined {
  return normalizeProgressAction(message);
}

function normalizeAgentProgressScreenshot(message: WsConsoleMessage): string | undefined {
  return normalizeProgressScreenshot(message);
}

function normalizeAgentProgressResult(message: WsConsoleMessage): string | undefined {
  return normalizeProgressResult(message);
}

function normalizeStatusRecordKey(message: WsConsoleMessage): string | null {
  return normalizeStatusTaskId(message);
}

function normalizeStepRecordKey(message: WsConsoleMessage): string | null {
  return normalizeAgentStepProgressKey(message);
}

function normalizeProgressMessageRecordKey(message: WsConsoleMessage): string | null {
  return normalizeProgressRecordKey(message);
}

function normalizeProgressMessageRecordId(progressMessageIds: Record<string, string>, message: WsConsoleMessage): string | null {
  return normalizeProgressRecordId(progressMessageIds, message);
}

function normalizeProgressMessageConversationState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeConversationWithProgress(state, message);
}

function normalizeStepMessageConversationState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeConversationWithStep(state, message);
}

function normalizeStatusMessageConversationState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeStatusConversationState(state, message);
}

function normalizeResetProgressTracking() {
  return resetProgressMap();
}

function normalizeResetTransientState() {
  return resetTransientProgressState();
}

function normalizeMessageProgressKey(message: WsConsoleMessage): string | null {
  return normalizeProgressKeyValue(message);
}

function normalizeMessageStepKey(message: WsConsoleMessage): string | null {
  return normalizeAgentStepProgressKey(message);
}

function normalizeMessageTaskStatus(message: WsConsoleMessage): string | undefined {
  return message.status;
}

function normalizeStatusShouldForget(message: WsConsoleMessage): boolean {
  return shouldForgetMessagesAfterStatus(message.status);
}

function normalizeStatusShouldStop(message: WsConsoleMessage): boolean {
  return isTerminalAgentStatus(message.status);
}

function normalizeCurrentTaskId(taskId: string | null): string | null {
  return taskId;
}

function normalizeStateCurrentTaskId(messageTaskId: string | null, currentTaskId: string | null): string | null {
  return updateCurrentTaskIdFromMessage(messageTaskId, currentTaskId);
}

function normalizeThinkingPanelState(message: WsConsoleMessage) {
  return getThinkingPanelStateFromProgress(message);
}

function normalizeTerminalStatusHistory(history: ChatMessage[], message: WsConsoleMessage): ChatMessage[] {
  return ensureStatusMessage(applyStatusToState(history, message), message);
}

function normalizeTaskIdFromProgressKey(progressKey: string): string {
  return progressKey.split(':')[0];
}

function normalizeTaskIdMatchesProgress(taskId: string | null, progressKey: string): boolean {
  return Boolean(taskId && normalizeTaskIdFromProgressKey(progressKey) === taskId);
}

function normalizeFilteredProgressMap(progressMessageIds: Record<string, string>, taskId: string | null): Record<string, string> {
  return removeTaskProgressTracking(progressMessageIds, taskId);
}

function normalizeTerminalConversationState(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return {
    conversationHistory: normalizeTerminalStatusHistory(state.conversationHistory, message),
    progressMessageIds: normalizeFilteredProgressMap(state.progressMessageIds, normalizeStatusTaskId(message)),
  };
}

function normalizeProgressConversationResult(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeProgressMessageConversationState(state, message);
}

function normalizeStepConversationResult(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeStepMessageConversationState(state, message);
}

function normalizeStatusConversationResult(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeStatusMessageConversationState(state, message);
}

function normalizeProgressOrNoop(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return shouldTrackProgressMessage(message)
    ? normalizeProgressConversationResult(state, message)
    : buildNoProgressUpsertResult(state);
}

function normalizeStandaloneStepIfNeeded(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return shouldShowStepAsSeparateBubble(state, message)
    ? appendStandaloneStepInState(state, message)
    : normalizeAgentStepConversationState(state, message);
}

function normalizeTerminalOrOngoingStatus(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return isTerminalAgentStatus(message.status)
    ? normalizeTerminalConversationState(state, message)
    : normalizeStatusConversationResult(state, message);
}

function normalizeHistoryPatchForStep(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return shouldUseExistingProgressMessage(state, message)
    ? normalizeAgentStepConversationState(state, message)
    : appendStandaloneStepInState(state, message);
}

function normalizeHistoryPatchForProgress(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeProgressOrNoop(state, message);
}

function normalizeHistoryPatchForStatus(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeTerminalOrOngoingStatus(state, message);
}

function normalizeCurrentStepPatch(message: WsConsoleMessage): AgentStep {
  return setCurrentStepFromAgentStep(message);
}

function normalizeCurrentStepNumPatch(message: WsConsoleMessage): number {
  return message.step_number ?? 0;
}

function normalizeCurrentTaskIdPatch(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return normalizeStateCurrentTaskId(normalizeMessageTaskId(message), currentTaskId);
}

function normalizeProgressMessageState(
  message: WsConsoleMessage,
  progressMessageIds: Record<string, string>,
  conversationHistory: ChatMessage[],
) {
  return normalizeProgressMessageConversationState({ progressMessageIds, conversationHistory }, message);
}

function normalizeAgentStepState(
  message: WsConsoleMessage,
  progressMessageIds: Record<string, string>,
  conversationHistory: ChatMessage[],
) {
  return normalizeStepMessageConversationState({ progressMessageIds, conversationHistory }, message);
}

function normalizeAgentStatusState(
  message: WsConsoleMessage,
  progressMessageIds: Record<string, string>,
  conversationHistory: ChatMessage[],
) {
  return normalizeStatusMessageConversationState({ progressMessageIds, conversationHistory }, message);
}

function normalizeEmptyProgressResult(
  conversationHistory: ChatMessage[],
  progressMessageIds: Record<string, string>,
) {
  return buildProgressUpsertResult(conversationHistory, progressMessageIds, null);
}

function normalizeShouldTrackProgress(message: WsConsoleMessage): boolean {
  return shouldTrackProgressMessage(message);
}

function normalizeProgressUpsert(
  message: WsConsoleMessage,
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
) {
  return normalizeShouldTrackProgress(message)
    ? normalizeProgressMessageConversationState(state, message)
    : normalizeEmptyProgressResult(state.conversationHistory, state.progressMessageIds);
}

function normalizeProgressTaskKey(taskId?: string | null, stepNumber?: number): string | null {
  return getProgressKey(taskId || undefined, stepNumber);
}

function normalizeHistoryMessages(history: ChatMessage[]): ChatMessage[] {
  return history;
}

function normalizeProgressMessageHistory(
  history: ChatMessage[],
  message: WsConsoleMessage,
  progressKey: string,
  messageId: string,
): ChatMessage[] {
  return addOrUpdateProgressConversationEntry(history, message, progressKey, messageId);
}

function normalizeHasProgressMessage(history: ChatMessage[], progressKey: string): boolean {
  return hasProgressMessage(history, progressKey);
}

function normalizeMergedProgressMessage(existing: ChatMessage, message: WsConsoleMessage): ChatMessage {
  return mergeProgressConversationMessage(existing, message);
}

function normalizeProgressUpdate(message: WsConsoleMessage): Partial<ChatMessage> {
  return buildProgressConversationUpdates(message);
}

function normalizeStandaloneStepMessage(message: WsConsoleMessage): ChatMessage {
  return buildAgentStepChatMessage(message);
}

function normalizeTerminalStatusMessage(message: WsConsoleMessage): ChatMessage {
  return buildStandaloneStatusMessage(message);
}

function normalizeTaskMessageTaskId(message: WsConsoleMessage): string | null {
  return normalizeMessageTaskId(message);
}

function normalizeTaskMessageErrorType(message: WsConsoleMessage): string | undefined {
  return normalizeMessageErrorType(message);
}

function normalizeTaskMessageSuccess(message: WsConsoleMessage): boolean | undefined {
  return normalizeMessageSuccess(message);
}

function normalizeTaskMessageScreenshot(message: WsConsoleMessage): string | undefined {
  return normalizeMessageScreenshot(message);
}

function normalizeTaskMessageReasoning(message: WsConsoleMessage): string | undefined {
  return normalizeMessageReasoning(message);
}

function normalizeTaskMessageAction(message: WsConsoleMessage): AgentAction | undefined {
  return normalizeMessageAction(message);
}

function normalizeTaskMessageResult(message: WsConsoleMessage): string | undefined {
  return normalizeMessageResult(message);
}

function normalizeTaskMessageError(message: WsConsoleMessage): string | undefined {
  return normalizeMessageError(message);
}

function normalizeMessageShouldSetScreenshot(message: WsConsoleMessage): boolean {
  return shouldSetScreenshot(message);
}

function normalizeMessageShouldSetBusy(message: WsConsoleMessage): boolean {
  return shouldUpdateDeviceBusyStatus(message);
}

function normalizeMessageShouldClearPhase(message: WsConsoleMessage): boolean {
  return message.type === 'agent_step' || isTerminalAgentStatus(message.status);
}

function normalizeResetStateAfterConversationClear() {
  return resetProgressMap();
}

function normalizeProgressHistory(messages: ChatMessage[]): ChatMessage[] {
  return messages;
}

function normalizeTaskCreatedHistory(): ChatMessage[] {
  return [];
}

function normalizeClearConversationHistory(): ChatMessage[] {
  return [];
}

function normalizeTaskCreatedCurrentScreenshot(): string | null {
  return null;
}

function normalizeTaskCreatedCurrentApp(): string {
  return '未知';
}

function normalizeTaskCreatedCurrentStep(): AgentStep | null {
  return null;
}

function normalizeTaskCreatedHistorySteps(): AgentStep[] {
  return [];
}

function normalizeTaskCreatedCurrentStepNum(): number {
  return 0;
}

function normalizeProgressCurrentStepNum(message: WsConsoleMessage): number {
  return message.step_number ?? 0;
}

function normalizeProgressCurrentTaskIdValue(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return normalizeProgressCurrentTaskId(message, currentTaskId);
}

function normalizeStepCurrentTaskIdValue(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return normalizeAgentStepCurrentTaskId(message, currentTaskId);
}

function normalizeStatusCurrentTaskIdValue(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return normalizeAgentStatusCurrentTaskId(message, currentTaskId);
}

function normalizeProgressDevicePatch() {
  return buildDeviceBusyStatePatch();
}

function normalizeStepDevicePatch() {
  return buildDeviceBusyStatePatch();
}

function normalizeCompletedDevicePatch(currentDeviceStatus?: string) {
  return buildDeviceIdleStatePatch(currentDeviceStatus);
}

function normalizeFailedDevicePatch() {
  return buildDeviceErrorStatePatch();
}

function normalizeInterruptedDevicePatch(currentDeviceStatus?: string) {
  return buildDeviceIdleStatePatch(currentDeviceStatus);
}

function normalizeProgressTaskIdValue(message: WsConsoleMessage): string | null {
  return normalizeProgressTaskId(message);
}

function normalizeStatusTaskIdValue(message: WsConsoleMessage): string | null {
  return normalizeStatusTaskId(message);
}

function normalizeCurrentDevicePatchForStatus(message: WsConsoleMessage, currentDeviceStatus?: string) {
  if (normalizeTaskFinishedState(message)) return normalizeCompletedDevicePatch(currentDeviceStatus);
  if (normalizeTaskFailedState(message)) return normalizeFailedDevicePatch();
  if (normalizeTaskInterruptedState(message)) return normalizeInterruptedDevicePatch(currentDeviceStatus);
  return normalizeProgressDevicePatch();
}

function normalizeCurrentTaskError(message: WsConsoleMessage): string | null {
  if (normalizeTaskFailedState(message)) return message.message || 'Task failed';
  return null;
}

function normalizeShouldAddStatusMessage(message: WsConsoleMessage): boolean {
  return shouldKeepStatusOnlyMessage(message);
}

function normalizeShouldForgetState(message: WsConsoleMessage): boolean {
  return normalizeStatusShouldForget(message);
}

function normalizeTerminalProgressCleanup(message: WsConsoleMessage, progressMessageIds: Record<string, string>): Record<string, string> {
  return shouldClearProgressAfterStatus(message)
    ? normalizeProgressMapAfterStatus(progressMessageIds, message)
    : progressMessageIds;
}

function normalizeConversationStateAfterStatus(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return {
    conversationHistory: normalizeConversationWithStatus(state, message),
    progressMessageIds: normalizeTerminalProgressCleanup(message, state.progressMessageIds),
  };
}

function normalizeConversationStateAfterProgress(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeConversationWithProgress(state, message);
}

function normalizeConversationStateAfterStep(
  state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> },
  message: WsConsoleMessage,
) {
  return normalizeConversationWithStep(state, message);
}

function normalizeStepShouldUpdateCurrentStep(): boolean {
  return true;
}

function normalizeProgressShouldUpdateCurrentStepNum(): boolean {
  return true;
}

function normalizeStepShouldUpdateCurrentStepNum(): boolean {
  return true;
}

function normalizeStatusShouldUpdateCurrentTaskId(message: WsConsoleMessage): boolean {
  return Boolean(normalizeStatusTaskId(message));
}

function normalizeProgressShouldUpdateCurrentTaskId(message: WsConsoleMessage): boolean {
  return Boolean(normalizeProgressTaskId(message));
}

function normalizeStepShouldUpdateCurrentTaskId(message: WsConsoleMessage): boolean {
  return Boolean(normalizeAgentStepTaskId(message));
}

function normalizeTaskCreatedTaskId(message: WsConsoleMessage): string | undefined {
  return message.task_id;
}

function normalizeTaskCreatedRunning(): boolean {
  return true;
}

function normalizeTaskCreatedStatus(): 'running' {
  return 'running';
}

function normalizeTaskCreatedProgressResetMap(): Record<string, string> {
  return resetProgressMap();
}

function normalizeTaskCreatedTransientState() {
  return resetTransientProgressState();
}

function normalizeTaskCreatedBaseState() {
  return {
    currentStepNum: normalizeTaskCreatedCurrentStepNum(),
    history: normalizeTaskCreatedHistorySteps(),
    currentStep: normalizeTaskCreatedCurrentStep(),
    currentScreenshot: normalizeTaskCreatedCurrentScreenshot(),
    currentApp: normalizeTaskCreatedCurrentApp(),
    canInterrupt: true,
    canResume: false,
    progressMessageIds: normalizeTaskCreatedProgressResetMap(),
    ...normalizeTaskCreatedTransientState(),
  };
}

function normalizeClearConversationState() {
  return {
    conversationHistory: normalizeClearConversationHistory(),
    progressMessageIds: normalizeResetStateAfterConversationClear(),
  };
}

function normalizeProgressSessionInit(savedHistory: ChatMessage[]) {
  const conversationHistory = normalizeSavedConversation(savedHistory);
  return {
    conversationHistory,
    progressMessageIds: progressTrackingFromConversation(conversationHistory),
    ...transientStateFromConversation(conversationHistory),
  };
}

function normalizeProgressSessionInitError() {
  return {
    conversationHistory: [],
    progressMessageIds: normalizeResetProgressTracking(),
    ...normalizeResetTransientState(),
  };
}

function normalizeProgressSendStart() {
  return {
    pendingAction: null,
    waitingForConfirm: false,
    waitingConfirmPhase: null,
    progressMessageIds: normalizeResetProgressTracking(),
    ...normalizeResetTransientState(),
  };
}

function normalizeProgressTaskInterrupt() {
  return normalizeInterruptState();
}

function normalizeProgressTaskEnd() {
  return normalizeEndSessionState();
}

function normalizeProgressTaskCreate() {
  return normalizeTaskCreatedBaseState();
}

function normalizeMessageStepNumber(message: WsConsoleMessage): number {
  return message.step_number ?? 0;
}

function normalizeProgressTaskTaskId(message: WsConsoleMessage): string | null {
  return normalizeProgressTaskIdValue(message);
}

function normalizeProgressTaskStep(message: WsConsoleMessage): number {
  return normalizeMessageStepNumber(message);
}

function normalizeAgentStepTaskTaskId(message: WsConsoleMessage): string | null {
  return normalizeAgentStepTaskId(message);
}

function normalizeAgentStatusTaskTaskId(message: WsConsoleMessage): string | null {
  return normalizeStatusTaskIdValue(message);
}

function normalizeCurrentTaskIdMessage(message: WsConsoleMessage): string | null {
  return normalizeMessageTaskId(message);
}

function normalizeTaskMessageContent(message: WsConsoleMessage): string {
  return normalizeStatusContent(message);
}

function normalizeTaskMessagePhase(message: WsConsoleMessage): 'reason' | 'act' | 'observe' {
  return normalizeProgressPhase(message);
}

function normalizeTaskMessageStage(message: WsConsoleMessage): string {
  return normalizeProgressStage(message);
}

function normalizeTaskMessageStatus(message: WsConsoleMessage): string | undefined {
  return message.status;
}

function normalizeTaskMessageData(message: WsConsoleMessage): Record<string, any> | undefined {
  return message.data;
}

function normalizeTaskMessageVersion(message: WsConsoleMessage): number | undefined {
  return message.version;
}

function normalizeTaskMessageControllerId(message: WsConsoleMessage): string | undefined {
  return message.controller_id;
}

function normalizeTaskMessageType(message: WsConsoleMessage): string {
  return message.type;
}

function normalizeShouldUseProgressBubble(message: WsConsoleMessage): boolean {
  return message.type === 'agent_progress';
}

function normalizeShouldUseStepBubble(message: WsConsoleMessage): boolean {
  return message.type === 'agent_step';
}

function normalizeShouldUseStatusBubble(message: WsConsoleMessage): boolean {
  return message.type === 'agent_status';
}

function normalizeMessageToProgressKey(message: WsConsoleMessage): string | null {
  return normalizeProgressKeyValue(message);
}

function normalizeMessageToStepKey(message: WsConsoleMessage): string | null {
  return normalizeAgentStepProgressKey(message);
}

function normalizeMessageToTaskId(message: WsConsoleMessage): string | null {
  return normalizeMessageTaskId(message);
}

function normalizeBaseChatMessage(content: string): ChatMessage {
  return {
    id: generateId(),
    role: 'agent',
    content,
    timestamp: new Date().toISOString(),
  };
}

function normalizeResultChatMessage(content: string, taskId?: string | null): ChatMessage {
  return {
    ...normalizeBaseChatMessage(content),
    taskId: taskId || undefined,
    isCompleted: true,
  };
}

function normalizeShouldAppendTerminalMessage(history: ChatMessage[], taskId: string | null): boolean {
  return shouldAppendStandaloneStatusMessage(history, taskId);
}

function normalizeAppendTerminalMessage(history: ChatMessage[], message: WsConsoleMessage): ChatMessage[] {
  return normalizeShouldAppendTerminalMessage(history, normalizeStatusTaskId(message))
    ? [...history, buildStandaloneStatusMessage(message)]
    : history;
}

function normalizeProgressTrackingState(progressMessageIds: Record<string, string>): Record<string, string> {
  return progressMessageIds;
}

function normalizeAgentStepTrackingState(progressMessageIds: Record<string, string>, message: WsConsoleMessage): Record<string, string> {
  return removeStepProgressTracking(progressMessageIds, message);
}

function normalizeAgentStatusTrackingState(progressMessageIds: Record<string, string>, message: WsConsoleMessage): Record<string, string> {
  return updateProgressMapForStatus(progressMessageIds, message);
}

function normalizeTaskCreatedTrackingState(): Record<string, string> {
  return resetProgressMap();
}

function normalizeMessageProgressSuccess(message: WsConsoleMessage): boolean | undefined {
  return normalizeProgressSuccess(message);
}

function normalizeMessageProgressError(message: WsConsoleMessage): string | undefined {
  return normalizeProgressError(message);
}

function normalizeMessageProgressStatus(message: WsConsoleMessage): string {
  return normalizeProgressStatus(message);
}

function normalizeMessageProgressContent(message: WsConsoleMessage): string {
  return normalizeAgentProgressMessage(message);
}

function normalizeMessageProgressAction(message: WsConsoleMessage): AgentAction | undefined {
  return normalizeAgentProgressAction(message);
}

function normalizeMessageProgressThinking(message: WsConsoleMessage): string | undefined {
  return normalizeAgentProgressThinking(message);
}

function normalizeMessageProgressResult(message: WsConsoleMessage): string | undefined {
  return normalizeAgentProgressResult(message);
}

function normalizeMessageProgressScreenshot(message: WsConsoleMessage): string | undefined {
  return normalizeAgentProgressScreenshot(message);
}

function normalizeMessageProgressErrorType(message: WsConsoleMessage): string | undefined {
  return normalizeAgentProgressErrorType(message);
}

function normalizeMessageProgressPhaseValue(message: WsConsoleMessage): 'reason' | 'act' | 'observe' {
  return normalizeAgentProgressPhase(message);
}

function normalizeMessageProgressStageValue(message: WsConsoleMessage): string {
  return normalizeAgentProgressStage(message);
}

function normalizeMessageProgressTaskIdValue(message: WsConsoleMessage): string | null {
  return normalizeAgentProgressTaskId(message);
}

function normalizeMessageProgressStepValue(message: WsConsoleMessage): number {
  return normalizeAgentProgressStep(message);
}

function normalizeMessageStatusTaskId(message: WsConsoleMessage): string | null {
  return normalizeAgentStatusTaskTaskId(message);
}

function normalizeMessageStepTaskId(message: WsConsoleMessage): string | null {
  return normalizeAgentStepTaskTaskId(message);
}

function normalizeMessageStatusErrorType(message: WsConsoleMessage): string | undefined {
  return normalizeAgentStatusErrorType(message);
}

function normalizeMessageStatusContentValue(message: WsConsoleMessage): string {
  return normalizeAgentStatusMessage(message);
}

function normalizeMessageStepContentValue(message: WsConsoleMessage): string {
  return normalizeAgentStepContent(message);
}

function normalizeProgressStoreState(message: WsConsoleMessage, currentTaskId: string | null) {
  return normalizeProgressBaseState(message, currentTaskId);
}

function normalizeStepStoreState(message: WsConsoleMessage, currentTaskId: string | null) {
  return normalizeStepBaseState(message, currentTaskId);
}

function normalizeStatusStoreState(message: WsConsoleMessage, currentTaskId: string | null) {
  return normalizeStatusBaseState(message, currentTaskId);
}

function normalizeNewProgressMessage(message: WsConsoleMessage, progressKey: string, messageId: string): ChatMessage {
  return buildProgressChatMessage(message, progressKey, messageId);
}

function normalizeExistingProgressMessage(existing: ChatMessage, message: WsConsoleMessage): ChatMessage {
  return updateExistingProgressMessage(existing, message);
}

function normalizeTerminalHistory(history: ChatMessage[], message: WsConsoleMessage): ChatMessage[] {
  return normalizeAppendTerminalMessage(applyStatusToState(history, message), message);
}

function normalizeHistoryAfterProgress(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeProgressMessageConversationState(state, message);
}

function normalizeHistoryAfterStep(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeAgentStepConversationState(state, message);
}

function normalizeHistoryAfterStatus(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeAgentStatusConversationState(state, message);
}

function normalizeCurrentStepPatchValue(message: WsConsoleMessage): AgentStep {
  return normalizeCurrentStepPatch(message);
}

function normalizeCurrentStepNumPatchValue(message: WsConsoleMessage): number {
  return normalizeCurrentStepNumPatch(message);
}

function normalizeMessageTaskPatch(message: WsConsoleMessage, currentTaskId: string | null): string | null {
  return normalizeCurrentTaskIdPatch(message, currentTaskId);
}

function normalizeProgressRecordIdValue(progressMessageIds: Record<string, string>, message: WsConsoleMessage): string | null {
  return normalizeMessageIdForProgress(progressMessageIds, message);
}

function normalizeProgressShouldFinalize(message: WsConsoleMessage): boolean {
  return shouldFinalizeProgressMessage(message);
}

function normalizeProgressShouldHideThinking(message: WsConsoleMessage): boolean {
  return shouldHideThinkingPanel(message);
}

function normalizeProgressShouldSetBusy(_message: WsConsoleMessage): boolean {
  return true;
}

function normalizeAgentStepShouldSetBusy(_message: WsConsoleMessage): boolean {
  return true;
}

function normalizeTerminalShouldClearProgress(message: WsConsoleMessage): boolean {
  return shouldClearProgressAfterStatus(message);
}

function normalizeStatusShouldSetIdle(message: WsConsoleMessage): boolean {
  return normalizeTaskFinishedState(message) || normalizeTaskInterruptedState(message);
}

function normalizeStatusShouldSetError(message: WsConsoleMessage): boolean {
  return normalizeTaskFailedState(message);
}

function normalizeStatusShouldSetBusyAgain(message: WsConsoleMessage): boolean {
  return isRunningStatus(message.status) || isPendingStatus(message.status);
}

function normalizeStatusShouldResetStep(message: WsConsoleMessage): boolean {
  return normalizeTaskFinishedState(message) || normalizeTaskFailedState(message) || normalizeTaskInterruptedState(message);
}

function normalizeStatusShouldForgetMessages(message: WsConsoleMessage): boolean {
  return normalizeTaskFinishedState(message);
}

function normalizeShouldSetCurrentTaskIdFromStatus(message: WsConsoleMessage): boolean {
  return Boolean(normalizeStatusTaskId(message));
}

function normalizeShouldSetCurrentTaskIdFromProgressMessage(message: WsConsoleMessage): boolean {
  return Boolean(normalizeProgressTaskId(message));
}

function normalizeShouldSetCurrentTaskIdFromAgentStep(message: WsConsoleMessage): boolean {
  return Boolean(normalizeAgentStepTaskId(message));
}

function normalizeTaskCreatedStatePatch(message: WsConsoleMessage) {
  return {
    currentTaskId: message.task_id,
    isRunning: true,
    status: 'running' as const,
    ...normalizeTaskCreatedBaseState(),
  };
}

function normalizeSessionInitState(savedHistory: ChatMessage[]) {
  return normalizeProgressSessionInit(savedHistory);
}

function normalizeSessionInitErrorState() {
  return normalizeProgressSessionInitError();
}

function normalizeSendCommandState() {
  return normalizeProgressSendStart();
}

function normalizeInterruptPatch() {
  return normalizeProgressTaskInterrupt();
}

function normalizeEndSessionPatch() {
  return normalizeProgressTaskEnd();
}

function normalizeClearConversationPatch() {
  return normalizeClearConversationState();
}

function normalizeTaskCreatedPatch(message: WsConsoleMessage) {
  return normalizeTaskCreatedStatePatch(message);
}

function normalizeAgentStepPatch(message: WsConsoleMessage) {
  return {
    currentStep: normalizeCurrentStepPatchValue(message),
    currentStepNum: normalizeCurrentStepNumPatchValue(message),
  };
}

function normalizeAgentProgressPatch(message: WsConsoleMessage, currentTaskId: string | null) {
  return {
    currentTaskId: normalizeProgressCurrentTaskIdValue(message, currentTaskId),
    currentStepNum: normalizeProgressCurrentStepNum(message),
    ...getThinkingPanelStateFromProgress(message),
  };
}

function normalizeAgentStatusPatch(message: WsConsoleMessage, currentTaskId: string | null, canInterrupt: boolean) {
  if (normalizeTaskFinishedState(message)) {
    return normalizeStatusCompletedState(message, currentTaskId);
  }
  if (normalizeTaskFailedState(message)) {
    return normalizeStatusFailedState(message, currentTaskId);
  }
  if (normalizeTaskInterruptedState(message)) {
    return normalizeStatusInterruptedState(message, currentTaskId);
  }
  if (isPendingStatus(message.status)) {
    return normalizeStatusPendingState(message, currentTaskId, canInterrupt);
  }
  if (isRunningStatus(message.status)) {
    return normalizeStatusRunningState(message, currentTaskId);
  }
  return normalizeStatusStoreState(message, currentTaskId);
}

function normalizeConversationPatchForProgress(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeHistoryAfterProgress(state, message);
}

function normalizeConversationPatchForStep(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeHistoryAfterStep(state, message);
}

function normalizeConversationPatchForStatus(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeHistoryAfterStatus(state, message);
}

function normalizeStatusDevicePatch(message: WsConsoleMessage, currentDeviceStatus?: string) {
  return normalizeCurrentDevicePatchForStatus(message, currentDeviceStatus);
}

function normalizeAgentStepHistoryPatch(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeAgentStepConversationState(state, message);
}

function normalizeAgentProgressHistoryPatch(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeProgressMessageConversationState(state, message);
}

function normalizeAgentStatusHistoryPatch(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeAgentStatusConversationState(state, message);
}

function normalizeProgressStatePatch(message: WsConsoleMessage, currentTaskId: string | null) {
  return normalizeAgentProgressPatch(message, currentTaskId);
}

function normalizeStepStatePatch(message: WsConsoleMessage) {
  return normalizeAgentStepPatch(message);
}

function normalizeStatusStatePatch(message: WsConsoleMessage, currentTaskId: string | null, canInterrupt: boolean) {
  return normalizeAgentStatusPatch(message, currentTaskId, canInterrupt);
}

function normalizeTaskCreatedStateValue(message: WsConsoleMessage) {
  return normalizeTaskCreatedPatch(message);
}

function normalizeConversationResetPatch() {
  return normalizeClearConversationPatch();
}

function normalizeHistoryInitPatch(savedHistory: ChatMessage[]) {
  return normalizeSessionInitState(savedHistory);
}

function normalizeHistoryInitErrorPatch() {
  return normalizeSessionInitErrorState();
}

function normalizeCommandStartPatch() {
  return normalizeSendCommandState();
}

function normalizeTaskInterruptPatch() {
  return normalizeInterruptPatch();
}

function normalizeTaskEndPatch() {
  return normalizeEndSessionPatch();
}

function normalizeMessageProgressPatch(message: WsConsoleMessage, currentTaskId: string | null) {
  return normalizeProgressStatePatch(message, currentTaskId);
}

function normalizeMessageStepPatch(message: WsConsoleMessage) {
  return normalizeStepStatePatch(message);
}

function normalizeMessageStatusPatch(message: WsConsoleMessage, currentTaskId: string | null, canInterrupt: boolean) {
  return normalizeStatusStatePatch(message, currentTaskId, canInterrupt);
}

function normalizeMessageHistoryProgressPatch(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeAgentProgressHistoryPatch(state, message);
}

function normalizeMessageHistoryStepPatch(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeAgentStepHistoryPatch(state, message);
}

function normalizeMessageHistoryStatusPatch(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeAgentStatusHistoryPatch(state, message);
}

function normalizeProgressStateValue(message: WsConsoleMessage, currentTaskId: string | null) {
  return normalizeMessageProgressPatch(message, currentTaskId);
}

function normalizeStepStateValue(message: WsConsoleMessage) {
  return normalizeMessageStepPatch(message);
}

function normalizeStatusStateValue(message: WsConsoleMessage, currentTaskId: string | null, canInterrupt: boolean) {
  return normalizeMessageStatusPatch(message, currentTaskId, canInterrupt);
}

function normalizeProgressHistoryValue(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeMessageHistoryProgressPatch(state, message);
}

function normalizeStepHistoryValue(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeMessageHistoryStepPatch(state, message);
}

function normalizeStatusHistoryValue(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeMessageHistoryStatusPatch(state, message);
}

function normalizeProgressPatchResult(message: WsConsoleMessage, currentTaskId: string | null) {
  return normalizeProgressStateValue(message, currentTaskId);
}

function normalizeStepPatchResult(message: WsConsoleMessage) {
  return normalizeStepStateValue(message);
}

function normalizeStatusPatchResult(message: WsConsoleMessage, currentTaskId: string | null, canInterrupt: boolean) {
  return normalizeStatusStateValue(message, currentTaskId, canInterrupt);
}

function normalizeProgressHistoryResult(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeProgressHistoryValue(state, message);
}

function normalizeStepHistoryResult(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeStepHistoryValue(state, message);
}

function normalizeStatusHistoryResult(state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeStatusHistoryValue(state, message);
}

function normalizeConversationStatePatch(type: 'progress' | 'step' | 'status', state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  if (type === 'progress') return normalizeProgressHistoryResult(state, message);
  if (type === 'step') return normalizeStepHistoryResult(state, message);
  return normalizeStatusHistoryResult(state, message);
}

function normalizeStorePatch(type: 'progress' | 'step' | 'status', message: WsConsoleMessage, currentTaskId: string | null, canInterrupt = false) {
  if (type === 'progress') return normalizeProgressPatchResult(message, currentTaskId);
  if (type === 'step') return normalizeStepPatchResult(message);
  return normalizeStatusPatchResult(message, currentTaskId, canInterrupt);
}

function normalizeConversationAfterMessage(type: 'progress' | 'step' | 'status', state: { conversationHistory: ChatMessage[]; progressMessageIds: Record<string, string> }, message: WsConsoleMessage) {
  return normalizeConversationStatePatch(type, state, message);
}

function normalizeStateAfterMessage(type: 'progress' | 'step' | 'status', message: WsConsoleMessage, currentTaskId: string | null, canInterrupt = false) {
  return normalizeStorePatch(type, message, currentTaskId, canInterrupt);
}

function normalizeTaskProgressMapReset() {
  return resetProgressMap();
}

function normalizeProgressMessageIds(): Record<string, string> {
  return {};
}

function normalizeConversationMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages;
}

function normalizeUseDeviceBusyPatch() {
  return { status: 'busy' as const };
}

function normalizeUseDeviceErrorPatch() {
  return { status: 'error' as const };
}

function normalizeUseDeviceIdlePatch(currentDeviceStatus?: string) {
  return { status: currentDeviceStatus === 'offline' ? 'offline' as const : 'idle' as const };
}

function normalizeAddUserMessage(content: string): ChatMessage {
  return {
    id: generateId(),
    role: 'user',
    content,
    timestamp: new Date().toISOString(),
  };
}

function normalizeAddAgentMessage(
  content: string,
  thinking?: string,
  action?: AgentAction,
  screenshot?: string,
  rawContent?: string,
  isParseError?: boolean,
): ChatMessage {
  return {
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
}

function normalizeStreamingMessage(): ChatMessage {
  return {
    id: generateId(),
    role: 'agent',
    content: '',
    timestamp: new Date().toISOString(),
    thinking: '',
  };
}

function normalizeAddMessageHistory(history: ChatMessage[], message: ChatMessage): ChatMessage[] {
  return [...history, message];
}

function normalizeUpdateMessageHistory(history: ChatMessage[], messageId: string, updates: Partial<ChatMessage>): ChatMessage[] {
  return history.map((msg) => (msg.id === messageId ? { ...msg, ...updates } : msg));
}

function normalizeForgetHistory(history: ChatMessage[], keepCount = MAX_MEMORY_ROUNDS): ChatMessage[] {
  const totalMessages = history.length;
  if (totalMessages <= keepCount * 2) {
    return history;
  }
  return history.slice(totalMessages - keepCount * 2);
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

export const agentStoreLegacyCompatRegistry = {
  shouldFinalizeOnAgentStep,
  normalizeFailureErrorType,
  shouldSyncProgressScreenshotOnStep,
  screenshotForStepMessage,
  normalizeStatusReason,
  normalizeStatusFinalReasoning,
  upsertProgressIntoHistory,
  updateStoreTransientForProgress,
  createProgressMessageId,
  normalizeFailureStateMessage,
  normalizeCompletionStateMessage,
  normalizeInterruptionStateMessage,
  progressTrackingFromState,
  withUpdatedProgressMessage,
  finalizeStepInState,
  shouldUpdateCurrentPhaseFromProgress,
  shouldResetPhaseOnStep,
  shouldResetPhaseOnStatus,
  withProgressMessage,
  getProgressKeyFromTaskStep,
  clearProgressOnSessionReset,
  normalizeInitialProgressMap,
  shouldUpdateBusyStatusFromStep,
  shouldSetCurrentTaskIdFromProgress,
  shouldSetCurrentTaskIdFromStep,
  shouldSetCurrentTaskIdFromStatus,
  upsertProgressMessageInHistory,
  finalizeProgressWithStep,
  appendStandaloneFailure,
  normalizeAgentStatusTaskId,
  normalizeTaskCreatedState,
  normalizeInitSessionState,
  normalizeInitErrorState,
  normalizeSendCommandStartState,
  shouldStatusUpdateDeviceIdle,
  shouldStatusUpdateDeviceError,
  shouldStatusUpdateDeviceBusy,
  normalizeStepProgressKeyValue,
  normalizeCurrentDeviceStatusForInterrupt,
  shouldTrackTaskCreatedProgressReset,
  normalizeMessageContentForFailure,
  normalizeMessageContentForCompletion,
  normalizeMessageContentForInterrupt,
  normalizeCurrentApp,
  normalizeProgressMessageId,
  normalizeProgressMapAfterStep,
  normalizeProgressMapAfterReset,
  shouldUseProgressState,
  shouldUseAgentStepState,
  shouldUseAgentStatusState,
  normalizeProgressTransientState,
  normalizeStepTransientState,
  normalizeStatusTransientState,
  normalizeProgressStateReset,
  normalizeProgressPanelState,
  normalizeStepPanelState,
  normalizeStatusPanelState,
  normalizeConversationCompletion,
  normalizeProgressConversationUpdate,
  normalizeProgressConversationEntry,
  normalizeStatusResultMessage,
  normalizeAgentProgressStatus,
  normalizeAgentProgressError,
  normalizeAgentProgressSuccess,
  normalizeStatusRecordKey,
  normalizeStepRecordKey,
  normalizeProgressMessageRecordKey,
  normalizeProgressMessageRecordId,
  normalizeMessageProgressKey,
  normalizeMessageStepKey,
  normalizeMessageTaskStatus,
  normalizeStatusShouldStop,
  normalizeCurrentTaskId,
  normalizeThinkingPanelState,
  normalizeTaskIdMatchesProgress,
  normalizeStepConversationResult,
  normalizeStandaloneStepIfNeeded,
  normalizeHistoryPatchForStep,
  normalizeHistoryPatchForProgress,
  normalizeHistoryPatchForStatus,
  normalizeProgressMessageState,
  normalizeAgentStepState,
  normalizeAgentStatusState,
  normalizeProgressUpsert,
  normalizeProgressTaskKey,
  normalizeHistoryMessages,
  normalizeProgressMessageHistory,
  normalizeHasProgressMessage,
  normalizeMergedProgressMessage,
  normalizeProgressUpdate,
  normalizeStandaloneStepMessage,
  normalizeTerminalStatusMessage,
  normalizeTaskMessageTaskId,
  normalizeTaskMessageErrorType,
  normalizeTaskMessageSuccess,
  normalizeTaskMessageScreenshot,
  normalizeTaskMessageReasoning,
  normalizeTaskMessageAction,
  normalizeTaskMessageResult,
  normalizeTaskMessageError,
  normalizeMessageShouldSetScreenshot,
  normalizeMessageShouldSetBusy,
  normalizeMessageShouldClearPhase,
  normalizeProgressHistory,
  normalizeTaskCreatedHistory,
  normalizeStepCurrentTaskIdValue,
  normalizeStatusCurrentTaskIdValue,
  normalizeStepDevicePatch,
  normalizeCurrentTaskError,
  normalizeShouldAddStatusMessage,
  normalizeShouldForgetState,
  normalizeConversationStateAfterStatus,
  normalizeConversationStateAfterProgress,
  normalizeConversationStateAfterStep,
  normalizeStepShouldUpdateCurrentStep,
  normalizeProgressShouldUpdateCurrentStepNum,
  normalizeStepShouldUpdateCurrentStepNum,
  normalizeStatusShouldUpdateCurrentTaskId,
  normalizeProgressShouldUpdateCurrentTaskId,
  normalizeStepShouldUpdateCurrentTaskId,
  normalizeTaskCreatedTaskId,
  normalizeTaskCreatedRunning,
  normalizeTaskCreatedStatus,
  normalizeProgressTaskCreate,
  normalizeProgressTaskTaskId,
  normalizeProgressTaskStep,
  normalizeCurrentTaskIdMessage,
  normalizeTaskMessageContent,
  normalizeTaskMessagePhase,
  normalizeTaskMessageStage,
  normalizeTaskMessageStatus,
  normalizeTaskMessageData,
  normalizeTaskMessageVersion,
  normalizeTaskMessageControllerId,
  normalizeTaskMessageType,
  normalizeShouldUseProgressBubble,
  normalizeShouldUseStepBubble,
  normalizeShouldUseStatusBubble,
  normalizeMessageToProgressKey,
  normalizeMessageToStepKey,
  normalizeMessageToTaskId,
  normalizeResultChatMessage,
  normalizeProgressTrackingState,
  normalizeAgentStepTrackingState,
  normalizeAgentStatusTrackingState,
  normalizeTaskCreatedTrackingState,
  normalizeMessageProgressSuccess,
  normalizeMessageProgressError,
  normalizeMessageProgressStatus,
  normalizeMessageProgressContent,
  normalizeMessageProgressAction,
  normalizeMessageProgressThinking,
  normalizeMessageProgressResult,
  normalizeMessageProgressScreenshot,
  normalizeMessageProgressErrorType,
  normalizeMessageProgressPhaseValue,
  normalizeMessageProgressStageValue,
  normalizeMessageProgressTaskIdValue,
  normalizeMessageProgressStepValue,
  normalizeMessageStatusTaskId,
  normalizeMessageStepTaskId,
  normalizeMessageStatusErrorType,
  normalizeMessageStatusContentValue,
  normalizeMessageStepContentValue,
  normalizeProgressStoreState,
  normalizeStepStoreState,
  normalizeNewProgressMessage,
  normalizeExistingProgressMessage,
  normalizeTerminalHistory,
  normalizeMessageTaskPatch,
  normalizeProgressRecordIdValue,
  normalizeProgressShouldFinalize,
  normalizeProgressShouldHideThinking,
  normalizeProgressShouldSetBusy,
  normalizeAgentStepShouldSetBusy,
  normalizeTerminalShouldClearProgress,
  normalizeStatusShouldSetIdle,
  normalizeStatusShouldSetError,
  normalizeStatusShouldSetBusyAgain,
  normalizeStatusShouldResetStep,
  normalizeStatusShouldForgetMessages,
  normalizeShouldSetCurrentTaskIdFromStatus,
  normalizeShouldSetCurrentTaskIdFromProgressMessage,
  normalizeShouldSetCurrentTaskIdFromAgentStep,
  normalizeConversationPatchForProgress,
  normalizeConversationPatchForStep,
  normalizeConversationPatchForStatus,
  normalizeStatusDevicePatch,
  normalizeTaskCreatedStateValue,
  normalizeConversationResetPatch,
  normalizeHistoryInitPatch,
  normalizeHistoryInitErrorPatch,
  normalizeCommandStartPatch,
  normalizeTaskInterruptPatch,
  normalizeTaskEndPatch,
  normalizeConversationAfterMessage,
  normalizeStateAfterMessage,
  normalizeTaskProgressMapReset,
  normalizeProgressMessageIds,
  normalizeConversationMessages,
  normalizeUseDeviceBusyPatch,
  normalizeUseDeviceErrorPatch,
  normalizeUseDeviceIdlePatch,
  normalizeAddUserMessage,
  normalizeAddAgentMessage,
  normalizeStreamingMessage,
  normalizeAddMessageHistory,
  normalizeUpdateMessageHistory,
  normalizeForgetHistory,
  buildStageChainPatchFromProgress,
  buildStageChainPatchFromStatus,
  buildStatePatchForProgressMessage,
  buildStatePatchForStatusMessage,
  buildStatePatchForSnapshot,
  shouldCreateVisibleStepBubble,
  shouldCreateVisibleStatusBubble,
  shouldCreateVisibleProgressBubble,
  buildTaskCreatedDerivedStatePatch,
  buildTransportAwareProgressMessage,
  buildTransportAwareStepMessage,
  buildTransportAwareStatusMessage,
  buildTransportAwareProgressUpdates,
  buildHydratedHistoryPatch,
  buildMessageVisibilityPatch,
  buildTaskActiveDerivedPatch,
  buildDisplayDerivedPatch,
  buildDerivedPatchFromConversation,
  buildTaskCreatedVisibilityPatch,
  buildCurrentStageChainPatch,
  buildConversationUIStatePatch,
  buildConversationFilterStatePatch,
  buildSnapshotUIStatePatch,
  buildBackendActivityPatch,
  buildDerivedResetPatch,
  buildResetPatch,
  buildConversationVisibilityPatch,
  buildActiveStatePatch,
  buildCurrentDerivedStatePatch,
  buildSnapshotRestorePatch,
  buildVisibleHistoryPatch,
  buildTaskActivityPatch,
  buildFilteredConversationStatePatch,
  buildActivityDerivedPatch,
  shouldUpdateBusyStatusFromProgress,
};

interface AgentState {
  // Current session
  currentDeviceId: string | null;
  progressMessageIds: Record<string, string>;
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
  maxObserveErrorRetries: number;

  // Error handling
  error: string | null;

  // 对话历史（气泡式对话）
  conversationHistory: ChatMessage[];
  displayConversationHistory: ChatMessage[];

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

  // Observe error decision state
  observeDecisionState: ObserveDecisionState;
  pendingObserveErrorDecision: ObserveErrorDecisionPayload | null;

  // WebSocket console callback reference
  _wsCallback: ((msg: WsConsoleMessage) => void) | null;

  // Derived backend-truth flags
  canInterrupt: boolean;
  canResume: boolean;
  isBackendTaskActive: boolean;
  stageChain: AgentStageChain;

  // Actions
  initSession: (deviceId: string, mode?: AgentMode) => void;
  endSession: () => void;
  setMode: (mode: AgentMode) => void;
  setMaxParseRetries: (retries: number) => void;
  setMaxObserveErrorRetries: (retries: number) => void;

  // Command - starts task (WS-driven, no polling)
  sendCommand: (command: string) => Promise<void>;
  submitObserveErrorDecision: (decision: 'continue' | 'interrupt', advice?: string) => Promise<void>;
  resolveObserveErrorDecisionCard: (messageId?: string) => void;

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
  _handleAgentProgress: (message: WsConsoleMessage) => void;
  upsertProgressMessage: (message: WsConsoleMessage) => string | null;
  createStreamingAgentMessage: () => string;
}

// 生成唯一Id
const generateId = () => `msg_${Date.now()}_${Math.random().toString(36).substr(2, 8)}`;

export const useAgentStore = create<AgentState>((set, get) => ({
  currentDeviceId: null,
  progressMessageIds: {},
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
  maxObserveErrorRetries: 2,
  error: null,
  conversationHistory: [],
  displayConversationHistory: [],
  currentScreenshot: null,
  currentApp: '未知',
  currentPhase: null,
  thinkingContent: '',
  isThinking: false,
  waitingForConfirm: false,
  waitingConfirmPhase: null,
  observeDecisionState: 'idle',
  pendingObserveErrorDecision: null,
  _wsCallback: null,
  canInterrupt: false,
  canResume: false,
  isBackendTaskActive: false,
  stageChain: createDefaultStageChain(),

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
      const restoredHistory = mergeRestoredConversation(
        snapshot.chat_history.map(toConversationMessage),
        savedHistory,
      );
      const hydratedHistory = normalizeConversationAfterSnapshot(snapshot, restoredHistory);
      const history = buildHistoryFromSnapshot(snapshot);
      const currentStep = getCurrentStepFromHistory(history);
      const normalizedStatus = normalizeStoreStatus(snapshot.status);
      const isRunning = hasActiveBackendTask(snapshot.task_id, snapshot.status, snapshot.can_interrupt);
      const transientState = transientStateFromConversation(hydratedHistory);

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
        maxObserveErrorRetries: snapshot.max_observe_error_retries ?? 2,
        error: null,
        ...buildSnapshotConversationStatePatch(snapshot, hydratedHistory),
        currentScreenshot: snapshot.current_screenshot || latestScreenshotFromConversation(hydratedHistory),
        currentApp: snapshot.current_app || '未知',
        waitingForConfirm: false,
        waitingConfirmPhase: null,
        canInterrupt: snapshot.can_interrupt,
        canResume: snapshot.can_resume,
        progressMessageIds: progressTrackingFromConversation(hydratedHistory),
        ...normalizeInitObserveDecisionState(snapshot, hydratedHistory),
        ...transientState,
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
        displayConversationHistory: [],
        currentScreenshot: null,
        currentApp: '未知',
        currentPhase: null,
        thinkingContent: '',
        isThinking: false,
        waitingForConfirm: false,
        waitingConfirmPhase: null,
        maxObserveErrorRetries: 2,
        ...normalizeEndSessionObserveDecisionState(),
        canInterrupt: false,
        canResume: false,
        isBackendTaskActive: false,
        stageChain: createDefaultStageChain(),
        progressMessageIds: normalizeResetProgressTracking(),
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
      displayConversationHistory: [],
      currentScreenshot: null,
      currentApp: '未知',
      currentPhase: null,
      thinkingContent: '',
      isThinking: false,
      waitingForConfirm: false,
      waitingConfirmPhase: null,
      canInterrupt: false,
      canResume: false,
      isBackendTaskActive: false,
      stageChain: createDefaultStageChain(),
      progressMessageIds: normalizeResetProgressTracking(),
    });
  },

  setMode: (mode) => {
    set({ mode });
  },

  setMaxParseRetries: (retries) => {
    set({ maxParseRetries: retries });
  },

  setMaxObserveErrorRetries: (retries) => {
    set({ maxObserveErrorRetries: retries });
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

      case 'agent_progress':
        if (message.device_id === deviceId) {
          state._handleAgentProgress(message);
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
          set((currentState) => buildStatePatchFromCurrent(currentState, {
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
            progressMessageIds: normalizeTaskCreatedProgressState(),
            ...normalizeTaskCreatedObserveDecisionState(),
            ...normalizeStatusStateReset(),
          }));
        }
        break;

      case 'observe_error_decision_applied':
        if (message.device_id === deviceId) {
          const pendingDecision = state.pendingObserveErrorDecision;
          const taskId = pendingDecision?.task_id || state.currentTaskId;
          const decisionCardMessageId = pendingDecision?.message_id;
          const appliedBubble = buildObserveErrorDecisionAppliedBubble({
            ...message,
            task_id: taskId || undefined,
          });
          const appliedSuccessfully = isObserveDecisionAppliedSuccess(message);

          set((currentState) => {
            let conversationHistory = [...currentState.conversationHistory, appliedBubble];

            if (appliedSuccessfully) {
              conversationHistory = clearObserveDecisionHistoryState(
                conversationHistory,
                taskId,
                decisionCardMessageId,
              );
            }

                return buildConversationAndDerivedPatch(currentState, conversationHistory, {
              ...(appliedSuccessfully
                ? buildObserveDecisionStatePatchFromHistory(conversationHistory)
                : {
                    observeDecisionState: 'pending' as const,
                    pendingObserveErrorDecision: currentState.pendingObserveErrorDecision,
                  }),
            });
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
    const nextHistory = [...state.conversationHistory, message];
    set((currentState) => buildConversationAndDerivedPatch(currentState, nextHistory));

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
    const nextHistory = [...state.conversationHistory, message];
    set((currentState) => buildConversationAndDerivedPatch(currentState, nextHistory));

    // 保存到 API (截图先保存到本地，之后可以上传)
    if (state.currentDeviceId) {
      saveMessageToAPI(state.currentDeviceId, 'agent', content, thinking, action, screenshot);
    }
  },

  // 更新Agent消息（用于流式更新）
  updateAgentMessage: (messageId: string, updates: Partial<ChatMessage>) => {
    set((state) => {
      const nextHistory = state.conversationHistory.map((msg) =>
        msg.id === messageId ? { ...msg, ...withTransportMetadataUpdates(updates) } : msg
      );
      return buildConversationAndDerivedPatch(state, nextHistory);
    });
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
    const nextHistory = [...state.conversationHistory, message];
    set((currentState) => buildConversationAndDerivedPatch(currentState, nextHistory));
    return message.id;
  },

  // 遗忘机制 - 保留最近的N轮对话
  forgetOldMessages: (keepCount = MAX_MEMORY_ROUNDS) => {
    set((state) => {
      if (state.currentTaskId && (state.status === 'running' || state.status === 'pending')) {
        return state;
      }

      const totalMessages = state.conversationHistory.length;
      const messagesToKeep = Math.max(keepCount * 4, keepCount);
      if (totalMessages <= messagesToKeep) {
        return state;
      }

      const newHistory = state.conversationHistory.slice(totalMessages - messagesToKeep);
      return buildConversationAndDerivedPatch(state, newHistory);
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
    set((currentState) => buildConversationAndDerivedPatch(currentState, [], {
      progressMessageIds: normalizeResetProgressTracking(),
      ...normalizeEndSessionObserveDecisionState(),
    }));
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
      ...normalizeSendCommandObserveDecisionState(),
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
        max_observe_error_retries: state.maxObserveErrorRetries,
      });

      agentStoreLogger.info('[sendCommand] Task starting', {
        taskId: result.task_id,
        deviceId: state.currentDeviceId,
      });

      set((currentState) => buildStatePatchFromCurrent(currentState, {
        currentTaskId: result.task_id || state.currentTaskId,
        isRunning: true,
        status: normalizeStoreStatus(result.status),
        currentStepNum: 0,
        history: [],
        currentStep: null,
        currentScreenshot: null,
        currentApp: '未知',
        canInterrupt: true,
        canResume: false,
      }));

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

  upsertProgressMessage: (message: WsConsoleMessage) => {
    const state = get();
    const progress = normalizeProgressMessageConversationState(
      {
        conversationHistory: state.conversationHistory,
        progressMessageIds: state.progressMessageIds,
      },
      message,
    );

    set((currentState) => buildConversationAndDerivedPatch(currentState, progress.conversationHistory, {
      progressMessageIds: progress.progressMessageIds,
      ...normalizeProgressStateWithObserveDecision(message, state.currentTaskId),
    }));

    return progress.messageId;
  },

  _handleAgentProgress: (message: WsConsoleMessage) => {
    const state = get();

    if (shouldSetScreenshot(message) && message.screenshot) {
      state.setScreenshot(message.screenshot);
    }

    state.upsertProgressMessage(message);

    agentStoreLogger.debug('[handleAgentProgress] Progress processed', {
      taskId: message.task_id,
      stepNumber: message.step_number,
      phase: message.phase,
      stage: message.stage,
    });
  },

  // Internal handler for agent_step WebSocket message
  _handleAgentStep: (message: WsConsoleMessage) => {
    const state = get();

    if (message.screenshot) {
      state.setScreenshot(message.screenshot);
    }

    const step = normalizeCurrentStep(message);
    state.setCurrentStep(step);
    state.appendStep(step);

    const conversationPatch = normalizeAgentStepConversationState(
      {
        conversationHistory: state.conversationHistory,
        progressMessageIds: state.progressMessageIds,
      },
      message,
    );

    set((currentState) => buildConversationAndDerivedPatch(currentState, conversationPatch.conversationHistory, {
      currentTaskId: normalizeAgentStepCurrentTaskId(message, state.currentTaskId),
      currentStepNum: normalizeAgentStepNumber(message),
      progressMessageIds: conversationPatch.progressMessageIds,
      ...updateStoreTransientForStep(),
    }));

    agentStoreLogger.debug('[handleAgentStep] Step processed', {
      stepNumber: message.step_number,
      hasAction: !!message.action,
      success: message.success,
      usedProgressMessage: shouldUseExistingProgressMessage(state, message),
    });
  },

  // Internal handler for agent_status WebSocket message
  _handleAgentStatus: (message: WsConsoleMessage) => {
    const state = get();
    const { status } = message;

    agentStoreLogger.info('[handleAgentStatus] Status update', {
      status,
      message: message.message,
      errorType: message.error_type || message.data?.error_type,
    });

    const conversationPatch = normalizeAgentStatusConversationState(
      {
        conversationHistory: state.conversationHistory,
        progressMessageIds: state.progressMessageIds,
      },
      message,
    );

    if (status === 'finished' || status === 'completed') {
      state.setCurrentStep(null);
      set((currentState) => buildConversationAndDerivedPatch(currentState, conversationPatch.conversationHistory, {
        ...normalizeStatusCompletedState(message, state.currentTaskId),
        progressMessageIds: conversationPatch.progressMessageIds,
        ...clearObserveDecisionSubmissionState(),
        ...updateStoreTransientForStatus(message),
      }));
      state.forgetOldMessages();
      return;
    }

    if (status === 'error' || status === 'failed') {
      state.setCurrentStep(null);
      set((currentState) => buildConversationAndDerivedPatch(currentState, conversationPatch.conversationHistory, {
        ...normalizeStatusFailedState(message, state.currentTaskId),
        error: normalizeFailureMessage(message),
        progressMessageIds: conversationPatch.progressMessageIds,
        ...clearObserveDecisionSubmissionState(),
        ...updateStoreTransientForStatus(message),
      }));
      return;
    }

    if (status === 'interrupted') {
      state.setCurrentStep(null);
      set((currentState) => buildConversationAndDerivedPatch(currentState, conversationPatch.conversationHistory, {
        ...normalizeStatusInterruptedState(message, state.currentTaskId),
        progressMessageIds: conversationPatch.progressMessageIds,
        ...clearObserveDecisionSubmissionState(),
        ...updateStoreTransientForStatus(message),
      }));

      return;
    }

    if (status === 'pending' || status === 'running' || status === 'waiting_confirmation') {
      set((currentState) => buildConversationAndDerivedPatch(currentState, conversationPatch.conversationHistory, {
        ...normalizeStatusStateWithObserveDecision(message, state.currentTaskId, state.canInterrupt),
        currentTaskId: normalizeAgentStatusCurrentTaskId(message, state.currentTaskId),
        progressMessageIds: conversationPatch.progressMessageIds,
        ...updateStoreTransientForStatus(message),
      }));
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
        step_number: step_number ?? 0,
      },
    });

    set((currentState) => buildStatePatchFromCurrent(currentState, {
      isRunning: true,
      waitingForConfirm: true,
      waitingConfirmPhase: 'act',
      status: 'waiting_confirmation',
      canInterrupt: true,
    }));
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

    set((currentState) => buildStatePatchFromCurrent(currentState, {
      currentPhase: phase,
      isThinking: phase === 'reason',
      thinkingContent: phase === 'reason' ? '思考中...' : '',
    }));
  },

  // Internal handler for agent_phase_end WebSocket message
  _handlePhaseEnd: (message: WsConsoleMessage) => {
    const phase = message.phase as 'reason' | 'act' | 'observe';
    agentStoreLogger.info('[handlePhaseEnd] Phase ended', { phase, stepNumber: message.step_number });

    set((currentState) => buildStatePatchFromCurrent(currentState, {
      currentPhase: phase,
      isThinking: false,
      thinkingContent: '',
    }));
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

    set((currentState) => buildStatePatchFromCurrent(currentState, {
      pendingAction: null,
      waitingForConfirm: false,
      waitingConfirmPhase: null,
      isRunning: true,
      status: approved ? 'running' : 'waiting_confirmation',
      canInterrupt: true,
    }));

    if (approved) {
      state.addAgentMessage(`用户确认执行 ${phaseToConfirm} 阶段`);
    } else {
      state.addAgentMessage(`用户取消执行 ${phaseToConfirm} 阶段`);
    }
  },

  confirmAction: async () => {
    get().confirmPhase(true);
  },

  rejectAction: async () => {
    get().confirmPhase(false);
  },

  skipAction: async () => {
    get().confirmPhase(false);
  },

  submitObserveErrorDecision: async (decision: 'continue' | 'interrupt', advice: string = '') => {
    const state = get();
    if (!state.currentDeviceId) {
      set({ error: 'No device selected' });
      return;
    }

    const trimmedAdvice = advice.trim();
    const pendingDecision = state.pendingObserveErrorDecision;

    set({ observeDecisionState: 'submitting' });

    const userMessage = buildObserveErrorDecisionUserMessage(decision, trimmedAdvice || undefined);
    state.addUserMessage(userMessage);

    wsConsoleApi.sendObserveErrorDecision(state.currentDeviceId, decision, trimmedAdvice);

    if (decision === 'interrupt') {
      set((currentState) => buildStatePatchFromCurrent(currentState, {
        isRunning: true,
        status: 'waiting_confirmation',
        canInterrupt: true,
        observeDecisionState: 'submitting',
        pendingObserveErrorDecision: pendingDecision,
      }));
      return;
    }

    set((currentState) => buildStatePatchFromCurrent(currentState, {
      isRunning: true,
      status: 'waiting_confirmation',
      canInterrupt: true,
      observeDecisionState: 'submitting',
      pendingObserveErrorDecision: pendingDecision,
    }));
  },

  resolveObserveErrorDecisionCard: (messageId?: string) => {
    const state = get();
    const resolution = normalizeObserveDecisionStateAfterResolution(messageId, state.currentTaskId);
    const conversationHistory = clearObserveDecisionHistoryState(
      state.conversationHistory,
      resolution.taskId,
      resolution.messageId,
    );

    set((currentState) => buildConversationAndDerivedPatch(currentState, conversationHistory, {
      ...buildObserveDecisionStatePatchFromHistory(conversationHistory),
    }));
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

      set((currentState) => buildStatePatchFromCurrent(currentState, {
        isRunning: true,
        pendingAction: null,
        waitingForConfirm: false,
        waitingConfirmPhase: null,
        status: currentState.status === 'waiting_confirmation' ? 'waiting_confirmation' : 'running',
        canInterrupt: true,
        ...normalizeInterruptObserveDecisionState(),
      }));
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
