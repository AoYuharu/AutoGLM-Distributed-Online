// Device Types
export type DeviceStatus = 'idle' | 'busy' | 'offline' | 'error';
export type DevicePlatform = 'android' | 'harmonyos' | 'ios';

export interface Device {
  id: string;
  device_id: string;
  client_id: string;
  platform: DevicePlatform;
  device_name: string;
  os_version: string;
  screen_width: number;
  screen_height: number;
  status: DeviceStatus;
  connection: 'usb' | 'wifi';
  last_seen: string;
  current_task_id?: string;
  current_app?: string;
  remark?: string;  // 设备备注
}

// Agent Types
export type AgentMode = 'cautious' | 'normal';
export type AgentPhase = 'reasoning' | 'action' | 'observation';
export type ActionType = 'tap' | 'double_tap' | 'long_press' | 'swipe' | 'type' | 'launch' | 'back' | 'home' | 'wait' | 'takeover';
export type MessageRole = 'user' | 'agent';

// 对话消息类型 - 气泡式对话
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  thinking?: string;  // Agent思考过程
  action?: AgentAction; // Agent执行的动作
  screenshot?: string;  // 对应的截图
  rawContent?: string;  // AI原始输出
  isParseError?: boolean;  // 是否为解析错误
  taskId?: string;
  stepNumber?: number;
  progressKey?: string;
  progressPhase?: 'reason' | 'act' | 'observe';
  progressStage?: string;
  progressMessage?: string;
  progressStatusText?: string;
  result?: string;
  success?: boolean;
  error?: string;
  errorType?: string;
  isProgressMessage?: boolean;
  isCompleted?: boolean;
}

// 历史步骤类型（兼容旧格式）
export interface AgentReasoning {
  thought: string;
  plan: string;
  confidence: number;
}

export interface AgentAction {
  type: ActionType;
  params: Record<string, any>;
  description: string;
}

export interface ScreenElement {
  id: string;
  text?: string;
  bounds: { x: number; y: number; width: number; height: number };
  clickable: boolean;
}

export interface AgentObservation {
  screenshot?: string;
  elements: ScreenElement[];
  app: string;
  description: string;
}

export interface AgentStep {
  id: string;
  phase: AgentPhase;
  reasoning?: AgentReasoning;
  action?: AgentAction;
  observation?: AgentObservation;
  timestamp: string;
  success?: boolean;
  error?: string;
  step_number?: number;
  thinking?: string;
}

export interface PendingAction {
  step: AgentStep;
  screenshot_url?: string;
  isMaxStepsPrompt?: boolean; // 是否为 max_steps 到达时的询问
}

// Log Types
export type LogLevel = 'info' | 'success' | 'warning' | 'error';
export type LogSource = 'chat_history' | 'react_records' | 'device_logs' | 'artifacts' | 'imported';

export interface LogEntry {
  id: string;
  timestamp: string;
  device_id: string;
  task_id?: string;
  type: string;
  level: LogLevel;
  message: string;
  details?: Record<string, any>;
  screenshot_url?: string;
  artifact_path?: string;
  download_url?: string;
  source?: LogSource;
  role?: string;
  phase?: string;
  step_number?: number;
}

export interface DeviceArtifacts {
  device_id: string;
  screenshots: string[];
  latest_screenshot: string | null;
  latest_screenshot_download: string | null;
  latest_log_download: string | null;
  react_records_download: string | null;
  chat_history_download: string | null;
}

// Task Types
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'interrupted';

export interface Task {
  id: string;
  task_id: string;
  device_id: string;
  instruction: string;
  status: TaskStatus;
  mode: AgentMode;
  max_steps: number;
  current_step: number;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  result?: {
    finish_message: string;
    total_steps: number;
    duration_seconds: number;
    final_screenshot?: string;
  };
}

// Batch Task Types
export interface BatchTaskItem {
  name: string;
  instruction: string;
  devices: string[];
  mode: AgentMode;
  max_steps?: number;
}

export interface BatchTaskConfig {
  mode: 'parallel' | 'sequential';
  tasks: BatchTaskItem[];
  settings: {
    stop_on_error: boolean;
    continue_on_timeout: boolean;
    notify_on_complete: boolean;
  };
}

// WebSocket Message Types
export interface WSMessage {
  msg_id: string;
  type: string;
  timestamp: string;
  version: string;
}

export interface DeviceStatusMessage extends WSMessage {
  type: 'device_status_changed';
  device_id: string;
  status: DeviceStatus;
  data: {
    previous_status: DeviceStatus;
    current_task_id?: string;
    device_info?: Partial<Device>;
  };
}

export interface AgentStepMessage extends WSMessage {
  type: 'agent_reasoning' | 'agent_pending_action' | 'agent_action_result';
  task_id: string;
  device_id: string;
  step: AgentStep;
}

export interface TaskUpdateMessage extends WSMessage {
  type: 'task_update';
  task_id: string;
  device_id: string;
  status: TaskStatus;
  progress: {
    current_step: number;
    max_steps: number;
    current_action?: string;
    current_app?: string;
    screenshot_url?: string;
  };
}

export interface TaskResultMessage extends WSMessage {
  type: 'task_result';
  task_id: string;
  device_id: string;
  status: 'completed' | 'failed' | 'interrupted';
  result: Task['result'];
}

// UI State Types
export type ViewMode = 'monitor' | 'agent' | 'batch';

export interface AppState {
  viewMode: ViewMode;
  selectedDeviceId: string | null;
  sidebarCollapsed: boolean;
  theme: 'light' | 'dark';
}

// Pending Device Types
export type PendingDeviceStatus = 'pending' | 'approved' | 'rejected';

export interface PendingDevice {
  id: string;
  device_id: string;
  client_id: string | null;
  platform: DevicePlatform;
  model: string | null;
  os_version: string | null;
  screen_width: number | null;
  screen_height: number | null;
  status: PendingDeviceStatus;
  reject_reason: string | null;
  created_at: string;
}
