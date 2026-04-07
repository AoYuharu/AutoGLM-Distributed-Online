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
import type { ChatMessage } from '../../types';

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

interface AgentWindowProps {
  deviceId: string;
  onClose?: () => void;
}

export const AgentWindow: React.FC<AgentWindowProps> = ({ deviceId, onClose }) => {
  const [inputValue, setInputValue] = useState('');
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
    status,
    conversationHistory,
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
  } = useAgentStore();

  // Get all screenshots from conversation history
  const allScreenshots = conversationHistory
    .map((msg, idx) => ({
      index: idx,
      screenshot: (msg as any).screenshot,
      timestamp: msg.timestamp,
    }))
    .filter(item => item.screenshot)
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
  }, [conversationHistory, pendingAction]);

  // Cleanup: 组件卸载时不再自动中断任务，避免误报
  // 任务状态由服务端管理，如果设备离线服务端会处理
  // useEffect(() => {
  //   return () => {
  //     if (isRunning) {
  //       interrupt();
  //     }
  //   };
  // }, [isRunning]);

  // canSendCommand: can send new command only when backend truth says no active task,
  // we hold the lock, and the device is idle.
  const hasBackendActiveTask = Boolean(currentTaskId) && (canInterrupt || isRunning || status === 'pending' || status === 'running');
  const canSendCommand = !hasBackendActiveTask && device?.status === 'idle';
  // 显示中断按钮的条件：有任务ID 且 可中断 且 任务状态不是已完成
  const showInterruptButton = canInterrupt && Boolean(currentTaskId) && status !== 'completed';
  const progressPercent = maxSteps > 0 ? Math.round((currentStepNum / maxSteps) * 100) : 0;

  const busyPlaceholder = hasBackendActiveTask
    ? '后端任务仍在进行或刚恢复，请等待...'
    : device?.status !== 'idle'
      ? `设备${device?.status === 'busy' ? '忙碌中' : device?.status}，请等待...`
      : '设备忙碌中，请等待...';

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
  const renderMessageBubble = (message: ChatMessage) => {
    const isUser = message.role === 'user';
    const isParseError = message.isParseError;

    return (
      <div
        key={message.id}
        className={clsx(
          'flex mb-3 animate-fadeIn',
          isUser ? 'flex-row-reverse' : 'flex-row'
        )}
      >
        {/* 头像 */}
        <div
          className={clsx(
            'w-9 h-9 rounded-full flex items-center justify-center text-white text-base flex-shrink-0 shadow-lg',
            isUser ? 'bg-gradient-to-br from-blue-400 to-blue-600 ml-2' : isParseError ? 'bg-gradient-to-br from-red-400 to-red-600 mr-2' : 'bg-gradient-to-br from-gray-500 to-gray-600 mr-2'
          )}
        >
          {isUser ? <UserOutlined /> : <RobotOutlined />}
        </div>

        {/* 消息内容 */}
        <div
          className={clsx(
            'max-w-75 flex flex-col',
            isUser ? 'items-end' : 'items-start'
          )}
        >
          {/* 用户名和时间 */}
          <div className={clsx('text-xs mb-1.5 mx-1 font-medium', isUser ? 'text-right text-blue-200' : 'text-left text-gray-500 dark:text-gray-400')}>
            {isUser ? '我' : 'Agent'} · {formatTime(message.timestamp)}
          </div>

          {/* 消息气泡 */}
          <div
            className={
              isUser
                ? 'px-5 py-4 text-sm whitespace-pre-wrap break-words shadow-xl max-w-90 bg-gradient-to-r from-blue-500 to-indigo-600 text-white rounded-xl'
                : isParseError
                ? 'px-5 py-4 text-sm whitespace-pre-wrap break-words shadow-xl max-w-90 bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-100 rounded-xl border-2 border-red-300 dark:border-red-700'
                : 'px-5 py-4 text-sm whitespace-pre-wrap break-words shadow-xl max-w-90 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 border-2 border-gray-200 dark:border-gray-700 rounded-xl'
            }
          >
            {message.content}
          </div>

          {/* 思考过程（仅Agent消息） */}
          {message.thinking && !isUser && (
            <div className="mt-2 ml-1 px-4 py-3.5 rounded-xl bg-purple-50 dark:bg-purple-900/60 border border-purple-200 dark:border-purple-700/60 text-xs max-w-90 shadow-md">
              <div className="text-purple-700 dark:text-purple-300 font-semibold mb-2 flex items-center gap-2">
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-purple-500 text-white text-xs shadow-sm">💭</span>
                思考过程
              </div>
              <div className="text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-words leading-relaxed">
                {message.thinking}
              </div>
            </div>
          )}

          {/* 动作信息（仅Agent消息） */}
          {message.action && !isUser && (
            <div className="mt-2 ml-1 px-4 py-3 rounded-xl bg-blue-50 dark:bg-blue-900/60 border border-blue-200 dark:border-blue-700/60 text-xs max-w-90 shadow-md">
              <div className="text-blue-700 dark:text-blue-300 font-semibold mb-2 flex items-center gap-2">
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-500 text-white text-xs shadow-sm">🎯</span>
                执行动作
              </div>
              <div className="flex items-center gap-2">
                <Tag color="blue" className="m-0">
                  {getActionTypeName(message.action.type)}
                </Tag>
              </div>
            </div>
          )}

          {/* 原始输出（仅解析错误时显示） */}
          {isParseError && message.rawContent && (
            <div className="mt-2 ml-1 px-4 py-3 rounded-xl bg-orange-100 dark:bg-orange-900/60 border border-orange-300 dark:border-orange-700/60 text-xs max-w-90 shadow-md">
              <div className="text-orange-700 dark:text-orange-300 font-semibold mb-2 flex items-center gap-2">
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-orange-500 text-white text-xs shadow-sm">⚠️</span>
                AI原始输出（解析失败）
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
                ⚠️ 达到最大步数
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
                  继续执行
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
                  停止任务
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
              ⚠️ 等待确认
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
                确认执行
              </Button>
              <Button
                danger
                size="small"
                icon={<CloseCircleOutlined />}
                onClick={() => rejectAction()}
              >
                拒绝
              </Button>
              <Button
                size="small"
                icon={<FastForwardOutlined />}
                onClick={() => skipAction()}
              >
                跳过
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
        <div className="flex items-center justify-between w-full min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <RobotOutlined />
            <span className="truncate">Agent 交互 - {device?.device_name || deviceId}</span>
            <Tag color={mode === 'cautious' ? 'orange' : 'blue'} className="m-0">
              {mode === 'cautious' ? '谨慎模式' : '非谨慎模式'}
            </Tag>
            {isRunning && (
              <Tag color="processing" icon={<Spin size="small" />} className="m-0">
                执行中
              </Tag>
            )}
            {!isLocked && (
              <Tag color="red" className="m-0">
                等待主控权
              </Tag>
            )}
            {/* Phase indicator */}
            {currentPhase && (
              <Tag color={currentPhase === 'reason' ? 'purple' : currentPhase === 'act' ? 'blue' : 'green'} className="m-0">
                {currentPhase === 'reason' ? '💭 思考中' : currentPhase === 'act' ? '🎯 执行中' : '👁 观察中'}
              </Tag>
            )}
          </div>
          <Space size={4}>
            <Tooltip title="清空对话">
              <Button
                type="text"
                icon={<DeleteOutlined />}
                onClick={clearConversation}
                danger
                size="small"
              />
            </Tooltip>
            <Tooltip title={isFullscreen ? '退出全屏' : '全屏'}>
              <Button
                type="text"
                icon={isFullscreen ? <CompressOutlined /> : <ExpandOutlined />}
                onClick={() => setIsFullscreen(!isFullscreen)}
                size="small"
              />
            </Tooltip>
            <Tooltip title="关闭">
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
                    src={`data:image/png;base64,${displayScreenshot}`}
                    alt="Screenshot"
                    style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
                  />
                  <div className="absolute inset-0 flex items-center justify-center opacity-0 hover:opacity-100 transition-opacity bg-black/20">
                    <ExpandOutlined className="text-white text-xl" />
                  </div>
                </>
              ) : (
                <div className="text-gray-400 text-center">
                  <div className="text-2xl">📱</div>
                  <div className="text-xs">等待截图</div>
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
                <Tooltip title="当前截图">
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
                      src={`data:image/png;base64,${currentScreenshot}`}
                      alt="Current"
                      style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                    />
                  </div>
                </Tooltip>
                {/* 历史截图 */}
                {allScreenshots.slice(0, 5).map((item) => (
                  <Tooltip title={`Step ${item.index + 1}`} key={item.index}>
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
                        src={`data:image/png;base64,${item.screenshot}`}
                        alt={`Step ${item.index + 1}`}
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
                <span>💭</span> AI思考过程
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
                ⚠️ 确认执行 {waitingConfirmPhase === 'reason' ? '思考' : waitingConfirmPhase === 'act' ? '动作' : '观察'} 阶段
              </div>
              <Space>
                <Button
                  type="primary"
                  size="small"
                  icon={<CheckCircleOutlined />}
                  onClick={() => confirmPhase(true)}
                  className="bg-green-500 border-green-500"
                >
                  确认
                </Button>
                <Button
                  danger
                  size="small"
                  icon={<CloseCircleOutlined />}
                  onClick={() => confirmPhase(false)}
                >
                  取消
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
            {conversationHistory.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-gray-400">
                <RobotOutlined className="text-6xl mb-4 opacity-20" />
                <div className="text-lg mb-2">开始对话吧!</div>
                <div className="text-sm">
                  在下方输入框中输入自然语言命令，Agent将帮你执行
                </div>
              </div>
            ) : (
              <>
                {conversationHistory.map((msg) => renderMessageBubble(msg))}
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

            {/* Input area */}
            <div className="flex items-end gap-3">
              <TextArea
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyPress}
                placeholder={
                  !isLocked
                    ? '等待主控权，其他控制台正在操作...'
                    : canSendCommand
                      ? '输入自然语言命令，让Agent帮你执行...'
                      : busyPlaceholder
                }
                autoSize={{ minRows: 1, maxRows: 3 }}
                disabled={!canSendCommand || !isLocked}
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
                    中断
                  </Button>
                ) : (
                  <>
                    <Button
                      type="primary"
                      icon={<SendOutlined />}
                      onClick={handleSend}
                      disabled={!canSendCommand || !inputValue.trim() || !isLocked}
                      block
                    >
                      发送
                    </Button>
                  </>
                )}
                {hasBackendActiveTask && !showInterruptButton && (
                  <Tag color="default" className="m-0 text-center">
                    后端任务恢复中
                  </Tag>
                )}
              </Space>
            </div>

            {/* Busy state warning - show when agent is running */}
            {hasBackendActiveTask && (
              <div className="mt-2 text-xs text-yellow-600">
                设备正在执行任务，无法发送新命令
              </div>
            )}

            {/* Session lock warning */}
            {!isLocked && (
              <div className="mt-2 text-xs text-red-600">
                主控权被其他控制台占用，请等待...
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
