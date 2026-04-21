import React, { useState, useRef, useEffect } from 'react';
import {
  Card,
  Input,
  Button,
  Progress,
  Radio,
  Spin,
  Tooltip,
  Space,
  Tag,
  Modal,
  Slider,
} from 'antd';
import {
  SendOutlined,
  CloseOutlined,
  ExpandOutlined,
  CompressOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  FastForwardOutlined,
  PauseOutlined,
  RobotOutlined,
  UserOutlined,
  MessageOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import clsx from 'clsx';
import { useAgentStore } from '../../stores/agentStore';
import { useDeviceStore } from '../../stores/deviceStore';
import type { AgentStageChain, AgentStageChainNode, ChatMessage } from '../../types';

const { TextArea } = Input;

// Action type display names
const getActionTypeName = (type: string): string => {
  const names: Record<string, string> = {
    tap: '点击',
    double_tap: '双击',
    long_press: '长按',
    swipe: '滑动',
    type: '输入',
    launch: '启动应用',
    back: '返回',
    home: '主页',
    wait: '等待',
    takeover: '人工接管',
    Launch: '启动应用',
    Tap: '点击',
    Swipe: '滑动',
    Type: '输入',
    Back: '返回',
    Home: '主页',
    Wait: '等待',
    finish: '完成',
  };
  return names[type] || type;
};

const getProgressPhaseName = (phase?: ChatMessage['progressPhase']) => {
  switch (phase) {
    case 'reason':
      return 'Reason';
    case 'act':
      return 'Act';
    case 'observe':
      return 'Observe';
    default:
      return '阶段';
  }
};

const getProgressTagColor = (message: ChatMessage) => {
  if (message.error || message.success === false) return 'error';
  if (message.isCompleted) return 'success';
  if (message.progressPhase === 'reason') return 'purple';
  if (message.progressPhase === 'act') return 'blue';
  if (message.progressPhase === 'observe') return 'green';
  return 'default';
};

const getProgressStageText = (message: ChatMessage) => {
  return message.progressStatusText || message.progressMessage || message.progressStage || '进行中';
};

const getBubbleContent = (message: ChatMessage) => {
  if (message.progressMessage && (!message.content || message.content === message.progressMessage)) {
    return message.progressMessage;
  }
  return message.content;
};

const shouldShowProgressBlock = (message: ChatMessage) => {
  if (message.isTransportProgress) return false;
  return Boolean(message.isProgressMessage || message.progressStage || message.progressMessage || message.progressPhase);
};

const shouldShowResultBlock = (message: ChatMessage) => {
  return Boolean(message.result && message.result !== message.content);
};

const shouldShowErrorBlock = (message: ChatMessage) => {
  return Boolean(message.error && message.error !== message.content);
};

const shouldShowScreenshotHint = (message: ChatMessage) => {
  return Boolean(message.screenshot);
};

const getProgressContainerClass = (message: ChatMessage) => {
  if (message.error || message.success === false) {
    return 'mt-2 ml-1 px-4 py-3 rounded-xl bg-red-50 dark:bg-red-900/40 border border-red-200 dark:border-red-700/60 text-xs max-w-90 shadow-md';
  }
  if (message.isCompleted) {
    return 'mt-2 ml-1 px-4 py-3 rounded-xl bg-green-50 dark:bg-green-900/40 border border-green-200 dark:border-green-700/60 text-xs max-w-90 shadow-md';
  }
  return 'mt-2 ml-1 px-4 py-3 rounded-xl bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700/60 text-xs max-w-90 shadow-md';
};

const getProgressTitleClass = (message: ChatMessage) => {
  if (message.error || message.success === false) {
    return 'text-red-700 dark:text-red-300 font-semibold mb-2 flex items-center gap-2';
  }
  if (message.isCompleted) {
    return 'text-green-700 dark:text-green-300 font-semibold mb-2 flex items-center gap-2';
  }
  return 'text-slate-700 dark:text-slate-300 font-semibold mb-2 flex items-center gap-2';
};

const getProgressDotClass = (message: ChatMessage) => {
  if (message.error || message.success === false) return 'bg-red-500';
  if (message.isCompleted) return 'bg-green-500';
  if (message.progressPhase === 'reason') return 'bg-purple-500';
  if (message.progressPhase === 'act') return 'bg-blue-500';
  if (message.progressPhase === 'observe') return 'bg-green-500';
  return 'bg-slate-500';
};

const getMessageBubbleClass = (isUser: boolean, isParseError: boolean) => {
  if (isUser) {
    return 'px-5 py-4 text-sm whitespace-pre-wrap break-words shadow-xl max-w-90 bg-gradient-to-r from-blue-500 to-indigo-600 text-white rounded-xl';
  }
  if (isParseError) {
    return 'px-5 py-4 text-sm whitespace-pre-wrap break-words shadow-xl max-w-90 bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-100 rounded-xl border-2 border-red-300 dark:border-red-700';
  }
  return 'px-5 py-4 text-sm whitespace-pre-wrap break-words shadow-xl max-w-90 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 border-2 border-gray-200 dark:border-gray-700 rounded-xl';
};

const getAvatarClass = (isUser: boolean, isParseError: boolean) => {
  if (isUser) return 'bg-gradient-to-br from-blue-400 to-blue-600 ml-2';
  if (isParseError) return 'bg-gradient-to-br from-red-400 to-red-600 mr-2';
  return 'bg-gradient-to-br from-gray-500 to-gray-600 mr-2';
};

const getNameClass = (isUser: boolean) => {
  return isUser ? 'text-right text-blue-200' : 'text-left text-gray-500 dark:text-gray-400';
};

const getItemsClass = (isUser: boolean) => {
  return isUser ? 'items-end' : 'items-start';
};

const getRowClass = (isUser: boolean) => {
  return isUser ? 'flex-row-reverse' : 'flex-row';
};

const getThinkingBlockTitle = () => '思考过程';
const getActionBlockTitle = () => '执行动作';
const getProgressBlockTitle = (message: ChatMessage) => `${getProgressPhaseName(message.progressPhase)} 状态`;
const getResultBlockTitle = () => '结果';
const getErrorBlockTitle = () => '错误';
const getScreenshotBlockTitle = () => '截图已更新';
const getScreenshotBlockText = () => '右侧截图区域已更新为当前步骤的最新截图。';
const getProgressSecondaryText = (message: ChatMessage) => message.progressMessage || message.content;
const getResultText = (message: ChatMessage) => message.result || '';
const getErrorText = (message: ChatMessage) => message.error || '';
const getStepLabel = (message: ChatMessage) => message.stepNumber ? `Step ${message.stepNumber}` : null;
const getCompletionText = (message: ChatMessage) => message.isCompleted ? '已完成' : '进行中';
const getCompletionColor = (message: ChatMessage) => (message.error || message.success === false ? 'error' : message.isCompleted ? 'success' : 'processing');
const getActionDescription = (message: ChatMessage) => message.action?.description || '';
const hasActionDescription = (message: ChatMessage) => Boolean(message.action?.description);
const isReasonBubble = (message: ChatMessage) => message.progressStage === 'reason' || message.progressStage === 'reason_start' || message.progressStage === 'reason_stream' || message.progressStage === 'reason_complete';
const isVisibleReasoningMessage = (message: ChatMessage) => {
  if (message.isTransportProgress) return false;
  if (isReasonBubble(message)) return true;
  return Boolean(!message.isProgressMessage && (message.thinking || message.action));
};
// Only show thinking/action blocks for visible reasoning/action messages, not for transport milestones.
const hasThinking = (message: ChatMessage, isUser: boolean) => Boolean(message.thinking && !isUser && isVisibleReasoningMessage(message));
const hasAction = (message: ChatMessage, isUser: boolean) => Boolean(message.action && !isUser && isVisibleReasoningMessage(message));
const hasRawContent = (message: ChatMessage, isParseError: boolean) => Boolean(isParseError && message.rawContent);
const getRawOutputTitle = () => 'AI原始输出（解析失败）';
const getResultContainerClass = 'mt-2 ml-1 px-4 py-3 rounded-xl bg-emerald-50 dark:bg-emerald-900/40 border border-emerald-200 dark:border-emerald-700/60 text-xs max-w-90 shadow-md';
const getErrorContainerClass = 'mt-2 ml-1 px-4 py-3 rounded-xl bg-red-50 dark:bg-red-900/40 border border-red-200 dark:border-red-700/60 text-xs max-w-90 shadow-md';
const getScreenshotContainerClass = 'mt-2 ml-1 px-4 py-3 rounded-xl bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700/60 text-xs max-w-90 shadow-md';
const getResultTitleClass = 'text-emerald-700 dark:text-emerald-300 font-semibold mb-2 flex items-center gap-2';
const getErrorTitleClass = 'text-red-700 dark:text-red-300 font-semibold mb-2 flex items-center gap-2';
const getScreenshotTitleClass = 'text-gray-700 dark:text-gray-300 font-semibold mb-2 flex items-center gap-2';
const getBlockTextClass = 'text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-words leading-relaxed';
const getSmallDotClass = (colorClass: string) => `inline-flex items-center justify-center w-2.5 h-2.5 rounded-full ${colorClass}`;
const getProgressDot = (message: ChatMessage) => getSmallDotClass(getProgressDotClass(message));
const getResultDot = () => getSmallDotClass('bg-emerald-500');
const getErrorDot = () => getSmallDotClass('bg-red-500');
const getScreenshotDot = () => getSmallDotClass('bg-gray-500');
const getThinkingDot = () => 'inline-flex items-center justify-center w-5 h-5 rounded-full bg-purple-500 text-white text-xs shadow-sm';
const getActionDot = () => 'inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-500 text-white text-xs shadow-sm';
const getRawOutputDot = () => 'inline-flex items-center justify-center w-5 h-5 rounded-full bg-orange-500 text-white text-xs shadow-sm';
const getThinkingIcon = () => '思';
const getActionIcon = () => '动';
const getRawOutputIcon = () => '警';

const getStageNodeLabel = (node: AgentStageChainNode) => {
  switch (node.key) {
    case 'reason':
      return '模型思考';
    case 'action':
      return '生成动作';
    case 'dispatch':
      return '下发设备';
    case 'wait_ack':
      return '等待确认';
    case 'wait_observe':
      return '等待观察';
    case 'observe':
      return '接收结果';
    default:
      return node.label;
  }
};

const getCompactPillClasses = (status: AgentStageChainNode['status']) => {
  switch (status) {
    case 'done':
      return {
        pill: 'border-emerald-300 bg-gradient-to-r from-emerald-50 to-teal-50 text-emerald-700 shadow-sm shadow-emerald-200/60 dark:border-emerald-700/70 dark:from-emerald-900/30 dark:to-teal-900/30 dark:text-emerald-300 dark:shadow-emerald-900/40',
        dot: 'bg-emerald-500',
      };
    case 'active':
      return {
        pill: 'border-blue-300 bg-gradient-to-r from-blue-50 via-indigo-50 to-violet-50 text-blue-700 shadow-md shadow-blue-300/50 ring-1 ring-blue-300/40 dark:border-blue-700/70 dark:from-blue-900/30 dark:via-indigo-900/30 dark:to-violet-900/30 dark:text-blue-200 dark:shadow-blue-900/40 dark:ring-blue-600/40',
        dot: 'bg-blue-500 animate-pulse',
      };
    case 'error':
      return {
        pill: 'border-red-300 bg-gradient-to-r from-red-50 to-rose-50 text-red-700 shadow-sm shadow-red-200/60 dark:border-red-700/70 dark:from-red-900/30 dark:to-rose-900/30 dark:text-red-300 dark:shadow-red-900/40',
        dot: 'bg-red-500',
      };
    default:
      return {
        pill: 'border-gray-200 bg-white/95 text-gray-500 dark:border-gray-700 dark:bg-gray-800/80 dark:text-gray-400',
        dot: 'bg-gray-300 dark:bg-gray-600',
      };
  }
};

const getCompactConnectorClass = (current: AgentStageChainNode['status'], previous?: AgentStageChainNode['status']) => {
  if (current === 'error' || previous === 'error') return 'bg-red-300 dark:bg-red-700';
  if (current === 'active' || previous === 'active') return 'bg-gradient-to-r from-blue-300 to-indigo-400 dark:from-blue-600 dark:to-indigo-500';
  if (current === 'done' && previous === 'done') return 'bg-gradient-to-r from-emerald-300 to-emerald-400 dark:from-emerald-700 dark:to-emerald-600';
  return 'bg-gray-200 dark:bg-gray-700';
};


const getStageNodeTooltip = (node: AgentStageChainNode, stageChain: AgentStageChain) => {
  const stepText = stageChain.stepNumber != null ? `第 ${stageChain.stepNumber} 步` : '等待步骤';
  const rawStageText = node.rawStage || stageChain.rawStage || '暂无阶段事件';
  const statusText = node.status === 'done'
    ? '已完成'
    : node.status === 'active'
      ? '进行中'
      : node.status === 'error'
        ? '异常'
        : '待执行';
  return `${getStageNodeLabel(node)} · ${statusText} · ${stepText} · ${rawStageText}`;
};

const getCompactPillLabel = (node: AgentStageChainNode) => {
  switch (node.key) {
    case 'reason': return '思考';
    case 'action': return '动作';
    case 'dispatch': return '下发';
    case 'wait_ack': return '确认';
    case 'wait_observe': return '观察';
    case 'observe': return '结果';
    default: return node.label.slice(0, 2);
  }
};

const renderCompactStageChain = (stageChain: AgentStageChain) => {
  const hasActiveNode = stageChain.nodes.some((n) => n.status === 'active');
  return (
    <div className="flex items-center gap-0">
      {stageChain.stepNumber != null && (
        <span
          className={clsx(
            'mr-1.5 rounded px-1 py-0.5 text-[10px] font-semibold transition-all duration-300',
            hasActiveNode
              ? 'bg-gradient-to-r from-blue-500 to-indigo-500 text-white shadow-sm shadow-blue-300/50'
              : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
          )}
        >
          S{stageChain.stepNumber}
        </span>
      )}
      {stageChain.nodes.map((node, index) => {
        const classes = getCompactPillClasses(node.status);
        const previous = index > 0 ? stageChain.nodes[index - 1] : undefined;
        const isActive = node.status === 'active';
        const connectorTouchesActive = node.status === 'active' || previous?.status === 'active';
        return (
          <React.Fragment key={node.key}>
            {index > 0 && (
              <div
                className={clsx(
                  'h-[2px] w-3 shrink-0',
                  getCompactConnectorClass(node.status, previous?.status),
                  connectorTouchesActive && 'animate-connectorPulse',
                )}
                aria-hidden="true"
              />
            )}
            <Tooltip title={getStageNodeTooltip(node, stageChain)}>
              <div
                className={clsx(
                  'relative inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] leading-tight transition-all duration-300',
                  classes.pill,
                  isActive && 'scale-[1.05]',
                )}
              >
                <span className={clsx('h-1.5 w-1.5 rounded-full', classes.dot)} />
                <span className="font-medium whitespace-nowrap">{getCompactPillLabel(node)}</span>
                {isActive && (
                  <span className="animate-shimmer pointer-events-none absolute inset-0 rounded-full" aria-hidden="true" />
                )}
              </div>
            </Tooltip>
          </React.Fragment>
        );
      })}
    </div>
  );
};


const getRunningTagLabel = (isBackendTaskActive: boolean) => (isBackendTaskActive ? '执行中' : '空闲');
const getRunningTagColor = (isBackendTaskActive: boolean) => (isBackendTaskActive ? 'processing' : 'default');
const getBackendBusyWarning = (isBackendTaskActive: boolean) => (
  isBackendTaskActive ? '设备正在执行后端任务，无法发送新命令' : null
);
const shouldRenderMessageBubble = (message: ChatMessage) => !message.isTransportProgress;
const getEmptyConversationHint = () => '在下方输入框中输入自然语言命令，Agent将帮你执行';
const getWaitingLockPlaceholder = () => '等待主控权，其他控制台正在操作...';
const getReadyPlaceholder = () => '输入自然语言命令，让Agent帮你执行...';
const getBusyPlaceholder = (isBackendTaskActive: boolean, deviceStatus?: string) => (
  isBackendTaskActive
    ? '后端任务执行中，请等待当前任务结束...'
    : deviceStatus !== 'idle'
      ? `设备${deviceStatus === 'busy' ? '忙碌中' : deviceStatus}，请等待...`
      : '设备忙碌中，请等待...'
);
const getBackendRecoveryTagText = (isBackendTaskActive: boolean) => (
  isBackendTaskActive ? '后端任务进行中' : ''
);
const getWaitConfirmTitle = (phase?: 'reason' | 'act' | 'observe' | null) => {
  const label = phase === 'reason' ? '思考' : phase === 'act' ? '动作' : '观察';
  return `确认执行 ${label} 阶段`;
};
const getPhaseTagText = (currentPhase: 'reason' | 'act' | 'observe' | null) => {
  if (currentPhase === 'reason') return '思考中';
  if (currentPhase === 'act') return '执行中';
  if (currentPhase === 'observe') return '观察中';
  return '';
};
const getPhaseTagColor = (currentPhase: 'reason' | 'act' | 'observe' | null) => {
  if (currentPhase === 'reason') return 'purple';
  if (currentPhase === 'act') return 'blue';
  if (currentPhase === 'observe') return 'green';
  return 'default';
};
const getThinkingPanelTitle = () => 'AI思考过程';
const getScreenshotPlaceholderIcon = () => '设备';
const getMaxStepsTitle = () => '达到最大步数';
const getWaitConfirmBannerTitle = (phase?: 'reason' | 'act' | 'observe' | null) => `等待确认 ${phase === 'reason' ? '思考' : phase === 'act' ? '动作' : '观察'} 阶段`;
const getBusyWarningText = (isBackendTaskActive: boolean) => getBackendBusyWarning(isBackendTaskActive);
const getNoScreenshotText = () => '等待截图';
const isCurrentScreenshotAvailable = (currentScreenshot: string | null) => Boolean(currentScreenshot);
const getCurrentScreenshotDataUrl = (currentScreenshot: string | null) => currentScreenshot ? `data:image/png;base64,${currentScreenshot}` : '';
const getHistoryScreenshotDataUrl = (screenshot: string | null | undefined) => screenshot ? `data:image/png;base64,${screenshot}` : '';
const getDisplayScreenshotDataUrl = (displayScreenshot: string | null) => displayScreenshot ? `data:image/png;base64,${displayScreenshot}` : '';
const getStartConversationTitle = () => '开始对话吧!';
const getUnlockWarningText = () => '主控权被其他控制台占用，请等待...';
const getInterruptButtonText = () => '中断';
const getSendButtonText = () => '发送';
const getObserveErrorTitle = () => 'Observe 错误处理';
const getClearConversationTitle = () => '清空对话';
const getFullscreenTooltip = (isFullscreen: boolean) => (isFullscreen ? '退出全屏' : '全屏');
const getCloseTooltip = () => '关闭';
const getCurrentScreenshotTooltip = () => '当前截图';
const getHistoryScreenshotTooltip = (index: number) => `Step ${index + 1}`;
const getHistoryScreenshotAlt = (index: number) => `Step ${index + 1}`;
const getCurrentScreenshotAlt = () => 'Current';
const getScreenshotAlt = () => 'Screenshot';
const getObserveDecisionContinueText = () => '继续任务';
const getObserveDecisionInterruptText = () => '中断任务';
const getObserveDecisionResolvedText = () => '已处理';
const getObserveDecisionPendingText = () => '等待决策';
const getObserveDecisionResolveText = () => '标记已处理';
const getObserveDecisionAdvicePlaceholder = () => '可选：给 AI 一条继续尝试的建议';
const getPendingInterruptText = () => '停止任务';
const getPendingContinueText = () => '继续执行';
const getPendingConfirmText = () => '确认执行';
const getPendingRejectText = () => '拒绝';
const getPendingSkipText = () => '跳过';
const getConfirmText = () => '确认';
const getCancelText = () => '取消';

interface AgentWindowProps {
  deviceId: string;
  onClose?: () => void;
}

export const AgentWindow: React.FC<AgentWindowProps> = ({ deviceId, onClose }) => {
  const [inputValue, setInputValue] = useState('');
  const [observeDecisionAdvice, setObserveDecisionAdvice] = useState('');
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [screenshotModalOpen, setScreenshotModalOpen] = useState(false);
  const [selectedScreenshotIndex, setSelectedScreenshotIndex] = useState(-1); // -1 means current
  const conversationRef = useRef<HTMLDivElement>(null);

  const device = useDeviceStore((state) => state.getDeviceById(deviceId));
  const {
    mode,
    setMode,
    isRunning,
    isLocked,
    pendingAction,
    sendCommand,
    confirmAction,
    rejectAction,
    skipAction,
    interrupt,
    currentStepNum,
    maxSteps,
    maxParseRetries,
    setMaxParseRetries,
    maxObserveErrorRetries,
    setMaxObserveErrorRetries,
    conversationHistory,
    displayConversationHistory,
    currentScreenshot,
    currentApp,
    clearConversation,
    currentPhase,
    thinkingContent,
    isThinking,
    waitingForConfirm,
    waitingConfirmPhase,
    confirmPhase,
    canInterrupt,
    currentTaskId,
    isBackendTaskActive,
    stageChain,
    observeDecisionState,
    submitObserveErrorDecision,
    resolveObserveErrorDecisionCard,
  } = useAgentStore();
  const resetObserveDecisionAdvice = () => setObserveDecisionAdvice('');

  type ConversationScreenshotEntry = {
    index: number;
    screenshot: ChatMessage['screenshot'];
    timestamp: ChatMessage['timestamp'];
  };

  const visibleConversationHistory = displayConversationHistory.filter(shouldRenderMessageBubble);

  // Get all screenshots from conversation history
  const allScreenshots: ConversationScreenshotEntry[] = conversationHistory
    .map((msg, idx) => ({
      index: idx,
      screenshot: msg.screenshot,
      timestamp: msg.timestamp,
    }))
    .filter((item): item is ConversationScreenshotEntry => Boolean(item.screenshot))
    .reverse(); // Most recent first

  // Get display screenshot based on selection
  const getDisplayScreenshot = () => {
    if (selectedScreenshotIndex === -1) {
      return currentScreenshot;
    }
    const found = allScreenshots.find(item => item.index === selectedScreenshotIndex);
    return found?.screenshot || currentScreenshot;
  };

  const displayScreenshot = getDisplayScreenshot();
  const hasMultipleScreenshots = allScreenshots.length > 0;

  // Auto-scroll to bottom of conversation
  useEffect(() => {
    if (conversationRef.current) {
      conversationRef.current.scrollTop = conversationRef.current.scrollHeight;
    }
  }, [visibleConversationHistory, pendingAction]);

  useEffect(() => {
    if (observeDecisionState === 'pending') {
      return;
    }

    const timer = window.setTimeout(() => {
      resetObserveDecisionAdvice();
    }, 0);

    return () => window.clearTimeout(timer);
  }, [observeDecisionState]);

  // Cleanup: 组件卸载时不再自动中断任务，避免误报
  // 任务状态由服务端管理，如果设备离线服务端会处理
  // useEffect(() => {
  //   return () => {
  //     if (isRunning) {
  //       interrupt();
  //     }
  //   };
  // }, [isRunning]);

  const canSendCommand = isLocked && !isBackendTaskActive && device?.status === 'idle';
  const showInterruptButton = isLocked && isBackendTaskActive && canInterrupt;
  const progressPercent = maxSteps > 0 ? Math.round((currentStepNum / maxSteps) * 100) : 0;
  const busyPlaceholder = getBusyPlaceholder(isBackendTaskActive, device?.status);
  const busyWarningText = getBusyWarningText(isBackendTaskActive);
  const backendRecoveryTagText = getBackendRecoveryTagText(isBackendTaskActive);
  const phaseTagText = getPhaseTagText(currentPhase);
  const currentScreenshotAvailable = isCurrentScreenshotAvailable(currentScreenshot);
  const hasActiveTaskId = Boolean(currentTaskId);
  const showRunningTag = isBackendTaskActive || isRunning || hasActiveTaskId;
  const showStageChain = isBackendTaskActive || stageChain.stepNumber != null || Boolean(stageChain.rawStage);
  const sendPlaceholder = !isLocked
    ? getWaitingLockPlaceholder()
    : canSendCommand
      ? getReadyPlaceholder()
      : busyPlaceholder;
  const inputDisabled = !canSendCommand || !isLocked;
  const showEmptyConversationState = visibleConversationHistory.length === 0 && !pendingAction;
  const shouldShowBackendRecoveryTag = Boolean(backendRecoveryTagText) && !showInterruptButton;
  const shouldShowBusyWarning = Boolean(busyWarningText) && isBackendTaskActive;
  const shouldShowCurrentScreenshotThumb = currentScreenshotAvailable && currentScreenshot;

  const handleSend = () => {
    if (!inputValue.trim() || !canSendCommand) return;
    sendCommand(inputValue);
    setInputValue('');
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // 格式化时间
  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  };

  // 渲染单条消息气泡
  const renderObserveErrorDecisionCard = (message: ChatMessage) => {
    const payload = message.observeErrorDecision;
    if (!payload || !message.isObserveErrorDecisionCard) {
      return null;
    }

    const isResolved = Boolean(message.observeErrorDecisionResolved);
    const isSubmitting = observeDecisionState === 'submitting' && !isResolved;

    return (
      <div className="mt-2 ml-1 px-4 py-3 rounded-xl bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700/60 text-xs max-w-90 shadow-md">
        <div className="text-amber-700 dark:text-amber-300 font-semibold mb-2 flex items-center gap-2">
          <span className="inline-flex items-center justify-center w-2.5 h-2.5 rounded-full bg-amber-500" />
          {getObserveErrorTitle()}
          <Tag color={isResolved ? 'success' : 'warning'} className="m-0 ml-1">
            {isResolved ? getObserveDecisionResolvedText() : getObserveDecisionPendingText()}
          </Tag>
        </div>
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <Tag color="orange" className="m-0">连续失败 {payload.consecutive_count} 次</Tag>
          <Tag color="gold" className="m-0">上限 {payload.max_retries} 次</Tag>
          <Tag color="default" className="m-0">Step {payload.step_number}</Tag>
        </div>
        <div className={getBlockTextClass}>{payload.message}</div>
        {!isResolved && (
          <div className="mt-3 space-y-3">
            <TextArea
              value={observeDecisionAdvice}
              onChange={(e) => setObserveDecisionAdvice(e.target.value)}
              placeholder={getObserveDecisionAdvicePlaceholder()}
              autoSize={{ minRows: 2, maxRows: 4 }}
              disabled={isSubmitting}
            />
            <Space wrap>
              <Button
                type="primary"
                size="small"
                loading={isSubmitting}
                onClick={() => {
                  submitObserveErrorDecision('continue', observeDecisionAdvice);
                  resetObserveDecisionAdvice();
                }}
                className="bg-green-500 border-green-500"
              >
                {getObserveDecisionContinueText()}
              </Button>
              <Button
                danger
                size="small"
                loading={isSubmitting}
                onClick={() => {
                  submitObserveErrorDecision('interrupt');
                  resetObserveDecisionAdvice();
                }}
              >
                {getObserveDecisionInterruptText()}
              </Button>
              <Button
                size="small"
                disabled={isSubmitting}
                onClick={() => {
                  resolveObserveErrorDecisionCard(message.id);
                  resetObserveDecisionAdvice();
                }}
              >
                {getObserveDecisionResolveText()}
              </Button>
            </Space>
          </div>
        )}
      </div>
    );
  };

  const renderMessageBubble = (message: ChatMessage) => {
    const isUser = message.role === 'user';
    const isParseError = message.isParseError;

    return (
      <div
        key={message.id}
        className={clsx('flex mb-3 animate-fadeIn', getRowClass(isUser))}
      >
        <div
          className={clsx(
            'w-9 h-9 rounded-full flex items-center justify-center text-white text-base flex-shrink-0 shadow-lg',
            getAvatarClass(isUser, !!isParseError)
          )}
        >
          {isUser ? <UserOutlined /> : <RobotOutlined />}
        </div>

        <div className={clsx('max-w-75 flex flex-col', getItemsClass(isUser))}>
          <div className={clsx('text-xs mb-1.5 mx-1 font-medium', getNameClass(isUser))}>
            {isUser ? '我' : 'Agent'} · {formatTime(message.timestamp)}
          </div>

          <div className={getMessageBubbleClass(isUser, !!isParseError)}>
            {getBubbleContent(message)}
          </div>

          {!isUser && shouldShowProgressBlock(message) && (
            <div className={getProgressContainerClass(message)}>
              <div className={getProgressTitleClass(message)}>
                <span className={getProgressDot(message)} />
                {getProgressBlockTitle(message)}
                {getStepLabel(message) && (
                  <Tag color={getProgressTagColor(message)} className="m-0 ml-1">
                    {getStepLabel(message)}
                  </Tag>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2 mb-2">
                <Tag color={getProgressTagColor(message)} className="m-0">
                  {getProgressStageText(message)}
                </Tag>
                <Tag color={getCompletionColor(message)} className="m-0">
                  {getCompletionText(message)}
                </Tag>
              </div>
              <div className={getBlockTextClass}>
                {getProgressSecondaryText(message)}
              </div>
            </div>
          )}

          {hasThinking(message, isUser) && (
            <div className="mt-2 ml-1 px-4 py-3.5 rounded-xl bg-purple-50 dark:bg-purple-900/60 border border-purple-200 dark:border-purple-700/60 text-xs max-w-90 shadow-md">
              <div className="text-purple-700 dark:text-purple-300 font-semibold mb-2 flex items-center gap-2">
                <span className={getThinkingDot()}>{getThinkingIcon()}</span>
                {getThinkingBlockTitle()}
              </div>
              <div className={getBlockTextClass}>
                {message.thinking}
              </div>
            </div>
          )}

          {hasAction(message, isUser) && (
            <div className="mt-2 ml-1 px-4 py-3 rounded-xl bg-blue-50 dark:bg-blue-900/60 border border-blue-200 dark:border-blue-700/60 text-xs max-w-90 shadow-md">
              <div className="text-blue-700 dark:text-blue-300 font-semibold mb-2 flex items-center gap-2">
                <span className={getActionDot()}>{getActionIcon()}</span>
                {getActionBlockTitle()}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Tag color="blue" className="m-0">
                  {getActionTypeName(message.action!.type)}
                </Tag>
                {hasActionDescription(message) && (
                  <span className="text-gray-700 dark:text-gray-200">{getActionDescription(message)}</span>
                )}
              </div>
            </div>
          )}

          {!isUser && shouldShowResultBlock(message) && (
            <div className={getResultContainerClass}>
              <div className={getResultTitleClass}>
                <span className={getResultDot()} />
                {getResultBlockTitle()}
              </div>
              <div className={getBlockTextClass}>{getResultText(message)}</div>
            </div>
          )}

          {!isUser && shouldShowErrorBlock(message) && (
            <div className={getErrorContainerClass}>
              <div className={getErrorTitleClass}>
                <span className={getErrorDot()} />
                {getErrorBlockTitle()}
              </div>
              <div className={getBlockTextClass}>{getErrorText(message)}</div>
            </div>
          )}

          {!isUser && shouldShowScreenshotHint(message) && (
            <div className={getScreenshotContainerClass}>
              <div className={getScreenshotTitleClass}>
                <span className={getScreenshotDot()} />
                {getScreenshotBlockTitle()}
              </div>
              <div className={getBlockTextClass}>{getScreenshotBlockText()}</div>
            </div>
          )}

          {!isUser && renderObserveErrorDecisionCard(message)}

          {hasRawContent(message, !!isParseError) && (
            <div className="mt-2 ml-1 px-4 py-3 rounded-xl bg-orange-100 dark:bg-orange-900/60 border border-orange-300 dark:border-orange-700/60 text-xs max-w-90 shadow-md">
              <div className="text-orange-700 dark:text-orange-300 font-semibold mb-2 flex items-center gap-2">
                <span className={getRawOutputDot()}>{getRawOutputIcon()}</span>
                {getRawOutputTitle()}
              </div>
              <div className="text-gray-800 dark:text-gray-200 whitespace-pre-wrap break-words leading-relaxed bg-white/80 dark:bg-black/40 p-2 rounded-lg font-mono text-[10px] max-h-40 overflow-y-auto">
                {message.rawContent}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  // 渲染待确认动作
  const renderPendingConfirmation = () => {
    // 显示谨慎模式的确认或 max_steps 到达时的询问
    if (!pendingAction) return null;

    // max_steps 到达时的确认 UI
    if (pendingAction.isMaxStepsPrompt) {
      return (
        <div className="flex mb-3">
          <div className="w-8 h-8 rounded-full bg-gray-400 text-white flex items-center justify-center text-sm mr-2">
            <RobotOutlined />
          </div>
          <div className="max-w-70 flex flex-col items-start">
            <div className="text-xs text-gray-400 mb-1">Agent · {formatTime(pendingAction.step.timestamp)}</div>
            <div className="px-4 py-3 rounded-2xl rounded-bl-md bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800 shadow-sm">
              <div className="text-orange-700 dark:text-orange-400 font-medium mb-2 flex items-center gap-2">
                {getMaxStepsTitle()}
              </div>
              <div className="text-sm text-gray-700 dark:text-gray-300 mb-3">
                {pendingAction.step.action?.description || `已达到最大步数，是否继续执行？`}
              </div>
              <Space>
                <Button
                  type="primary"
                  size="small"
                  icon={<CheckCircleOutlined />}
                  onClick={() => {
                    // 继续执行（增加步数）
                    useAgentStore.getState().continueTask?.(50);
                  }}
                  className="bg-green-500 border-green-500"
                >
                  {getPendingContinueText()}
                </Button>
                <Button
                  danger
                  size="small"
                  icon={<CloseCircleOutlined />}
                  onClick={() => {
                    // 停止任务
                    useAgentStore.getState().interrupt?.();
                  }}
                >
                  {getPendingInterruptText()}
                </Button>
              </Space>
            </div>
          </div>
        </div>
      );
    }

    // 谨慎模式确认 UI
    if (mode !== 'cautious') return null;

    return (
      <div className="flex mb-3">
        <div className="w-8 h-8 rounded-full bg-gray-400 text-white flex items-center justify-center text-sm mr-2">
          <RobotOutlined />
        </div>
        <div className="max-w-70 flex flex-col items-start">
          <div className="text-xs text-gray-400 mb-1">Agent · {formatTime(pendingAction.step.timestamp)}</div>
          <div className="px-4 py-3 rounded-2xl rounded-bl-md bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 shadow-sm">
            <div className="text-yellow-700 dark:text-yellow-400 font-medium mb-2 flex items-center gap-2">
              {getWaitConfirmBannerTitle(waitingConfirmPhase)}
            </div>
            <div className="text-sm text-gray-700 dark:text-gray-300 mb-3">
              即将执行: <Tag color="orange">{getActionTypeName(pendingAction.step.action?.type || 'tap')}</Tag>
              <span className="ml-2">{pendingAction.step.action?.description || ''}</span>
            </div>
            <Space>
              <Button
                type="primary"
                size="small"
                icon={<CheckCircleOutlined />}
                onClick={() => confirmAction()}
                className="bg-green-500 border-green-500"
              >
                {getPendingConfirmText()}
              </Button>
              <Button
                danger
                size="small"
                icon={<CloseCircleOutlined />}
                onClick={() => rejectAction()}
              >
                {getPendingRejectText()}
              </Button>
              <Button
                size="small"
                icon={<FastForwardOutlined />}
                onClick={() => skipAction()}
              >
                {getPendingSkipText()}
              </Button>
            </Space>
          </div>
        </div>
      </div>
    );
  };

  return (
    <Card
      className={clsx(
        'flex flex-col h-full',
        isFullscreen && 'fixed inset-4 z-50'
      )}
      styles={{
        body: {
          display: 'flex',
          flexDirection: 'column',
          padding: 0,
          overflow: 'hidden',
          height: '100%',
          maxHeight: 'calc(90vh - 120px)',
        },
      }}
      title={
        <div className="flex w-full min-w-0 items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <RobotOutlined />
            <span className="truncate">Agent 交互 - {device?.device_name || deviceId}</span>
            <Tag color={mode === 'cautious' ? 'orange' : 'blue'} className="m-0">
              {mode === 'cautious' ? '谨慎模式' : '非谨慎模式'}
            </Tag>
            {showRunningTag && (
              <Tag color={getRunningTagColor(isBackendTaskActive)} icon={isBackendTaskActive ? <Spin size="small" /> : undefined} className="m-0">
                {getRunningTagLabel(isBackendTaskActive)}
              </Tag>
            )}
            {!isLocked && (
              <Tag color="red" className="m-0">
                等待主控权
              </Tag>
            )}
            {phaseTagText && (
              <Tag color={getPhaseTagColor(currentPhase)} className="m-0">
                {phaseTagText}
              </Tag>
            )}
            {shouldShowBackendRecoveryTag && (
              <Tag color="default" className="m-0">
                {backendRecoveryTagText}
              </Tag>
            )}
          </div>
          <div className="shrink-0">
            {showStageChain ? renderCompactStageChain(stageChain) : null}
          </div>
          <Space size={4}>
            <Tooltip title={getClearConversationTitle()}>
              <Button
                type="text"
                icon={<DeleteOutlined />}
                onClick={clearConversation}
                danger
                size="small"
              />
            </Tooltip>
            <Tooltip title={getFullscreenTooltip(isFullscreen)}>
              <Button
                type="text"
                icon={isFullscreen ? <CompressOutlined /> : <ExpandOutlined />}
                onClick={() => setIsFullscreen(!isFullscreen)}
                size="small"
              />
            </Tooltip>
            <Tooltip title={getCloseTooltip()}>
              <Button
                type="text"
                icon={<CloseOutlined />}
                onClick={onClose}
                size="small"
              />
            </Tooltip>
          </Space>
        </div>
      }
    >
      {/* Main content area */}
      <div className="flex flex-row" style={{ flexShrink: 1, minWidth: 0, height: '100%', overflow: 'hidden' }}>
        {/* Left: Screenshot preview - 固定宽度，高度固定 */}
        <div
          className="border-r border-gray-200 dark:border-gray-700 flex flex-col bg-gray-50 dark:bg-gray-800"
          style={{ width: 200, flexShrink: 0, flexBasis: 'auto' }}
        >
          {/* 截图区域 - 放大高度 */}
          <div className="p-3 border-b border-gray-200 dark:border-gray-700 flex flex-col" style={{ height: 280, flexShrink: 0 }}>
            <div className="text-xs font-medium mb-2 flex items-center justify-between flex-shrink-0">
              <span className="flex items-center gap-1">
                <MessageOutlined />
                截图
                {selectedScreenshotIndex !== -1 && (
                  <Tag color="orange" className="ml-1 text-[10px]">历史</Tag>
                )}
              </span>
              {hasMultipleScreenshots && (
                <Button
                  type="link"
                  size="small"
                  icon={<ExpandOutlined />}
                  onClick={() => setScreenshotModalOpen(true)}
                  className="text-xs p-0 h-auto"
                >
                  查看全部
                </Button>
              )}
            </div>
            <div
              className="bg-gray-200 dark:bg-gray-700 rounded-lg flex items-center justify-center relative overflow-hidden cursor-pointer hover:opacity-90 transition-opacity"
              style={{ flex: 1, minHeight: 0 }}
              onClick={() => displayScreenshot && setScreenshotModalOpen(true)}
              title={displayScreenshot ? "点击查看大图" : ""}
            >
              {displayScreenshot ? (
                <>
                  <img
                    src={getDisplayScreenshotDataUrl(displayScreenshot)}
                    alt={getScreenshotAlt()}
                    style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
                  />
                  <div className="absolute inset-0 flex items-center justify-center opacity-0 hover:opacity-100 transition-opacity bg-black/20">
                    <ExpandOutlined className="text-white text-xl" />
                  </div>
                </>
              ) : (
                <div className="text-gray-400 text-center">
                  <div className="text-2xl">{getScreenshotPlaceholderIcon()}</div>
                  <div className="text-xs">{getNoScreenshotText()}</div>
                </div>
              )}
            </div>
          </div>

          {/* 截图历史列表 */}
          {hasMultipleScreenshots && (
            <div className="border-b border-gray-200 dark:border-gray-700 flex-shrink-0" style={{ maxHeight: 100, overflowY: 'auto' }}>
              <div className="text-xs text-gray-500 p-2 pb-1">截图历史</div>
              <div className="flex gap-1 px-2 pb-2 flex-wrap">
                {/* 当前截图 */}
                {shouldShowCurrentScreenshotThumb && (
                  <Tooltip title={getCurrentScreenshotTooltip()}>
                    <div
                      className={clsx(
                        'w-10 h-10 rounded cursor-pointer border-2 overflow-hidden transition-all',
                        selectedScreenshotIndex === -1
                          ? 'border-blue-500 opacity-100'
                          : 'border-transparent opacity-60 hover:opacity-80'
                      )}
                      onClick={() => setSelectedScreenshotIndex(-1)}
                    >
                      <img
                        src={getCurrentScreenshotDataUrl(currentScreenshot)}
                        alt={getCurrentScreenshotAlt()}
                        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      />
                    </div>
                  </Tooltip>
                )}
                {/* 历史截图 */}
                {allScreenshots.slice(0, 5).map((item) => (
                  <Tooltip title={getHistoryScreenshotTooltip(item.index)} key={item.index}>
                    <div
                      className={clsx(
                        'w-10 h-10 rounded cursor-pointer border-2 overflow-hidden transition-all',
                        selectedScreenshotIndex === item.index
                          ? 'border-blue-500 opacity-100'
                          : 'border-transparent opacity-60 hover:opacity-80'
                      )}
                      onClick={() => setSelectedScreenshotIndex(item.index)}
                    >
                      <img
                        src={getHistoryScreenshotDataUrl(item.screenshot)}
                        alt={getHistoryScreenshotAlt(item.index)}
                        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      />
                    </div>
                  </Tooltip>
                ))}
              </div>
            </div>
          )}

          {/* Current app info */}
          <div className="p-3 border-t border-gray-200 dark:border-gray-700 flex-shrink-0">
            <div className="bg-white dark:bg-gray-800 rounded-xl p-3 shadow-lg border-2 border-gray-200 dark:border-gray-700">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-2.5 h-2.5 rounded-full bg-green-500 animate-pulse"></div>
                <span className="text-xs text-gray-600 dark:text-gray-300 font-semibold">当前应用</span>
              </div>
              <div className="font-bold text-sm truncate text-gray-800 dark:text-gray-100">
                {currentApp || '未知'}
              </div>
            </div>
          </div>

          {/* Progress bar */}
          <div className="p-3 flex-shrink-0">
            <div className="bg-white dark:bg-gray-800 rounded-xl p-3 shadow-lg border-2 border-gray-200 dark:border-gray-700">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-gray-600 dark:text-gray-300 font-semibold">执行进度</span>
                <span className="text-xs font-bold text-blue-600 dark:text-blue-400">{currentStepNum}/{maxSteps}</span>
              </div>
              <Progress percent={progressPercent} size="small" strokeColor={{ '0%': '#3b82f6', '100%': '#8b5cf6' }} />
            </div>
          </div>
        </div>

        {/* Right: Chat panel */}
        <div className="flex flex-col" style={{ flex: 1, minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
          {/* Thinking content panel - shows during reason phase */}
          {isThinking && thinkingContent && (
            <div className="flex-shrink-0 px-4 py-3 bg-purple-50 dark:bg-purple-900/30 border-b border-purple-200 dark:border-purple-800">
              <div className="text-purple-700 dark:text-purple-300 text-xs font-semibold mb-1 flex items-center gap-1">
                <span>思</span> {getThinkingPanelTitle()}
              </div>
              <div className="text-gray-700 dark:text-gray-200 text-sm whitespace-pre-wrap max-h-24 overflow-y-auto">
                {thinkingContent}
              </div>
            </div>
          )}

          {/* Phase confirmation panel - shows when waiting for confirmation */}
          {waitingForConfirm && waitingConfirmPhase && (
            <div className="flex-shrink-0 px-4 py-3 bg-yellow-50 dark:bg-yellow-900/30 border-b border-yellow-200 dark:border-yellow-800">
              <div className="text-yellow-700 dark:text-yellow-300 text-sm font-semibold mb-2 flex items-center gap-2">
                提示 {getWaitConfirmTitle(waitingConfirmPhase)}
              </div>
              <Space>
                <Button
                  type="primary"
                  size="small"
                  icon={<CheckCircleOutlined />}
                  onClick={() => confirmPhase(true)}
                  className="bg-green-500 border-green-500"
                >
                  {getConfirmText()}
                </Button>
                <Button
                  danger
                  size="small"
                  icon={<CloseCircleOutlined />}
                  onClick={() => confirmPhase(false)}
                >
                  {getCancelText()}
                </Button>
              </Space>
            </div>
          )}

          {/* Conversation history - 可滚动区域 */}
          <div
            ref={conversationRef}
            className="overflow-y-auto p-4 bg-gray-50 dark:bg-gray-900"
            style={{ flex: '1 1 auto', minHeight: 0, maxHeight: 'calc(100% - 100px)' }}
          >
            {showEmptyConversationState ? (
              <div className="h-full flex flex-col items-center justify-center text-gray-400">
                <RobotOutlined className="text-6xl mb-4 opacity-20" />
                <div className="text-lg mb-2">{getStartConversationTitle()}</div>
                <div className="text-sm">
                  {getEmptyConversationHint()}
                </div>
              </div>
            ) : (
              <>
                {visibleConversationHistory.map((msg) => renderMessageBubble(msg))}
                {renderPendingConfirmation()}
              </>
            )}
          </div>

          {/* Bottom: Mode switch and input - 固定在底部 */}
          <div className="flex-shrink-0 p-4 border-t border-gray-200 dark:border-gray-700 bg-gradient-to-t from-white to-gray-50 dark:from-gray-800 dark:to-gray-900">
            {/* Mode switch */}
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm text-gray-500 font-medium">执行模式:</span>
              <Radio.Group
                value={mode}
                onChange={(e) => setMode(e.target.value)}
                size="small"
                className="font-medium"
              >
                <Radio.Button value="cautious" className="font-medium">谨慎模式</Radio.Button>
                <Radio.Button value="normal" className="font-medium">非谨慎模式</Radio.Button>
              </Radio.Group>
            </div>

            {/* Max parse retries slider */}
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm text-gray-500 font-medium">解析重试次数:</span>
              <div className="flex items-center gap-3">
                <Slider
                  min={0}
                  max={10}
                  value={maxParseRetries}
                  onChange={setMaxParseRetries}
                  style={{ width: 100 }}
                />
                <span className="text-xs text-gray-600 dark:text-gray-400 w-8">{maxParseRetries}次</span>
              </div>
            </div>

            <div className="flex items-center justify-between mb-3">
              <span className="text-sm text-gray-500 font-medium">Observe 失败重试:</span>
              <div className="flex items-center gap-3">
                <Slider
                  min={0}
                  max={10}
                  value={maxObserveErrorRetries}
                  onChange={setMaxObserveErrorRetries}
                  style={{ width: 100 }}
                />
                <span className="text-xs text-gray-600 dark:text-gray-400 w-8">{maxObserveErrorRetries}次</span>
              </div>
            </div>

            {/* Input area */}
            <div className="flex items-end gap-3">
              <TextArea
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyPress}
                placeholder={sendPlaceholder}
                autoSize={{ minRows: 1, maxRows: 3 }}
                disabled={inputDisabled}
                className="flex-1 rounded-xl"
              />
              <Space direction="vertical" size={4}>
                {showInterruptButton ? (
                  <Button
                    danger
                    icon={<PauseOutlined />}
                    onClick={interrupt}
                    disabled={!isLocked}
                    block
                  >
                    {getInterruptButtonText()}
                  </Button>
                ) : (
                  <Button
                    type="primary"
                    icon={<SendOutlined />}
                    onClick={handleSend}
                    disabled={inputDisabled || !inputValue.trim()}
                    block
                  >
                    {getSendButtonText()}
                  </Button>
                )}
                {shouldShowBackendRecoveryTag && (
                  <Tag color="default" className="m-0 text-center">
                    {backendRecoveryTagText}
                  </Tag>
                )}
              </Space>
            </div>

            {/* Busy state warning - show when agent is running */}
            {shouldShowBusyWarning && (
              <div className="mt-2 text-xs text-yellow-600">
                {busyWarningText}
              </div>
            )}

            {/* Session lock warning */}
            {!isLocked && (
              <div className="mt-2 text-xs text-red-600">
                {getUnlockWarningText()}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Custom styles for animation */}
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fadeIn {
          animation: fadeIn 0.3s ease-out;
        }
        @keyframes shimmer {
          0% { background-position: -100% 0; }
          100% { background-position: 200% 0; }
        }
        .animate-shimmer {
          background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.4) 50%, transparent 100%);
          background-size: 200% 100%;
          animation: shimmer 2s ease-in-out infinite;
        }
        @keyframes connectorPulse {
          0%, 100% { opacity: 0.6; }
          50% { opacity: 1; }
        }
        .animate-connectorPulse {
          animation: connectorPulse 1.5s ease-in-out infinite;
        }
      `}</style>

      {/* Screenshot fullscreen modal */}
      <Modal
        open={screenshotModalOpen}
        onCancel={() => setScreenshotModalOpen(false)}
        footer={null}
        width="auto"
        centered
        styles={{
          body: {
            padding: 0,
            background: 'transparent',
          },
          mask: {
            backgroundColor: 'rgba(0, 0, 0, 0.85)',
          },
        }}
        closable={false}
      >
        {displayScreenshot && (
          <div className="flex flex-col items-center">
            <img
              src={`data:image/png;base64,${displayScreenshot}`}
              alt="Screenshot Full"
              style={{
                maxWidth: '90vw',
                maxHeight: '80vh',
                objectFit: 'contain',
                borderRadius: '8px',
              }}
            />
            <div className="text-white/70 text-sm mt-3">
              当前应用: {currentApp}
            </div>
          </div>
        )}
      </Modal>
    </Card>
  );
};
