import { create } from 'zustand';
import type { DeviceArtifacts, LogEntry, LogLevel } from '../types';
import { logApi, type DeviceHistoryResponse } from '../services/api';
import { logStoreLogger } from '../hooks/useLogger';

interface LogState {
  logs: Record<string, LogEntry[]>;
  artifacts: Record<string, DeviceArtifacts | null>;
  latestTaskIds: Record<string, string | null>;
  loading: boolean;
  error: string | null;

  fetchLogs: (deviceId: string) => Promise<void>;
  importLogs: (deviceId: string, logs: LogEntry[]) => void;
  setLogs: (deviceId: string, logs: LogEntry[]) => void;

  getLogsForDevice: (deviceId: string) => LogEntry[];
  getArtifactsForDevice: (deviceId: string) => DeviceArtifacts | null;
  getLatestTaskIdForDevice: (deviceId: string) => string | null;
}

type AnyRecord = Record<string, any>;

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function toIsoTimestamp(value: unknown): string {
  if (!isNonEmptyString(value)) {
    return new Date(0).toISOString();
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return new Date(0).toISOString();
  }

  return date.toISOString();
}

function inferLevel(success?: unknown, error?: unknown, phase?: unknown): LogLevel {
  if (success === false || isNonEmptyString(error)) {
    return 'error';
  }

  if (phase === 'observe' || phase === 'act') {
    return 'success';
  }

  return 'info';
}

function buildArtifactUrl(deviceId: string, path?: unknown): string | undefined {
  if (!isNonEmptyString(path)) {
    return undefined;
  }

  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('/api/')) {
    return path;
  }

  return logApi.getArtifactFileUrl(deviceId, path);
}

function formatAction(action: unknown): string {
  if (!action || typeof action !== 'object') {
    return '执行动作';
  }

  const record = action as AnyRecord;
  const actionName = record.action || record.type || 'action';
  const extra = Object.entries(record)
    .filter(([key, value]) => key !== 'action' && key !== 'type' && value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join(', ');

  return extra ? `${actionName} (${extra})` : String(actionName);
}

function deriveTaskIdFromChat(message: AnyRecord): string | undefined {
  const explicitTaskId = message.task_id;
  if (isNonEmptyString(explicitTaskId)) {
    return explicitTaskId;
  }

  const messageId = message.id;
  if (!isNonEmptyString(messageId)) {
    return undefined;
  }

  const matched = messageId.match(/msg_(task_.+?)_(user|agent|system)$/);
  if (matched?.[1]) {
    return matched[1];
  }

  const fallbackMatched = messageId.match(/msg_(task_[^_]+)_/);
  return fallbackMatched?.[1];
}

function normalizeChatHistory(deviceId: string, chatHistory: AnyRecord[]): LogEntry[] {
  return chatHistory.map((message, index) => {
    const screenshotPath = message.screenshot_path;
    const timestamp = toIsoTimestamp(message.created_at);
    const role = isNonEmptyString(message.role) ? message.role : 'system';
    const details: AnyRecord = { ...message };

    return {
      id: `chat_${message.id || `${timestamp}_${index}`}`,
      timestamp,
      device_id: deviceId,
      task_id: deriveTaskIdFromChat(message),
      type: `chat_${role}`,
      level: role === 'agent' ? 'success' : 'info',
      message: isNonEmptyString(message.content) ? message.content : '(空消息)',
      details,
      screenshot_url: buildArtifactUrl(deviceId, screenshotPath),
      artifact_path: isNonEmptyString(screenshotPath) ? screenshotPath : undefined,
      download_url: buildArtifactUrl(deviceId, screenshotPath),
      source: 'chat_history',
      role,
    };
  });
}

function normalizeReactRecords(deviceId: string, reactRecords: AnyRecord[]): LogEntry[] {
  return reactRecords.map((record, index) => {
    const phase = isNonEmptyString(record.phase) ? record.phase : 'react';
    const screenshotPath = record.screenshot;
    const timestamp = toIsoTimestamp(record.timestamp);

    let message = '';
    if (phase === 'reason') {
      message = isNonEmptyString(record.reasoning) ? record.reasoning : formatAction(record.action);
    } else if (phase === 'act') {
      message = isNonEmptyString(record.action_result)
        ? record.action_result
        : formatAction(record.action);
    } else if (phase === 'observe') {
      message = isNonEmptyString(record.observation)
        ? record.observation
        : isNonEmptyString(record.error)
          ? record.error
          : '观察结果已记录';
    } else {
      message = isNonEmptyString(record.observation)
        ? record.observation
        : isNonEmptyString(record.reasoning)
          ? record.reasoning
          : formatAction(record.action);
    }

    return {
      id: `react_${timestamp}_${record.step_number ?? index}_${phase}_${index}`,
      timestamp,
      device_id: deviceId,
      task_id: isNonEmptyString(record.task_id) ? record.task_id : undefined,
      type: `react_${phase}`,
      level: inferLevel(record.success, record.error, phase),
      message,
      details: { ...record },
      screenshot_url: buildArtifactUrl(deviceId, screenshotPath),
      artifact_path: isNonEmptyString(screenshotPath) ? screenshotPath : undefined,
      download_url: buildArtifactUrl(deviceId, screenshotPath),
      source: 'react_records',
      phase,
      step_number: typeof record.step_number === 'number' ? record.step_number : undefined,
    };
  });
}

function parseJsonl(text: string): AnyRecord[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .flatMap((line) => {
      try {
        const parsed = JSON.parse(line);
        return typeof parsed === 'object' && parsed !== null ? [parsed as AnyRecord] : [];
      } catch (error) {
        logStoreLogger.warn('[parseJsonl] Skipping invalid JSONL line', { line });
        return [];
      }
    });
}

function normalizeDeviceLogs(deviceId: string, logText: string): LogEntry[] {
  return parseJsonl(logText).map((record, index) => {
    const timestamp = toIsoTimestamp(record.timestamp);
    const screenshotPath = record.screenshot;
    const message = isNonEmptyString(record.result)
      ? record.result
      : isNonEmptyString(record.error)
        ? record.error
        : `${record.type || 'device_log'} 已记录`;

    return {
      id: `device_log_${timestamp}_${record.task_id || 'no_task'}_${index}`,
      timestamp,
      device_id: deviceId,
      task_id: isNonEmptyString(record.task_id) ? record.task_id : undefined,
      type: isNonEmptyString(record.type) ? record.type : 'device_log',
      level: inferLevel(record.success, record.error),
      message,
      details: { ...record },
      screenshot_url: buildArtifactUrl(deviceId, screenshotPath),
      artifact_path: isNonEmptyString(screenshotPath) ? screenshotPath : undefined,
      download_url: buildArtifactUrl(deviceId, screenshotPath),
      source: 'device_logs',
      step_number: typeof record.step_number === 'number' ? record.step_number : undefined,
    };
  });
}

function mergeTimeline(deviceId: string, history: DeviceHistoryResponse, artifacts: DeviceArtifacts | null, latestLogText: string): LogEntry[] {
  const timeline = [
    ...normalizeChatHistory(deviceId, history.chat_history || []),
    ...normalizeReactRecords(deviceId, history.react_records || []),
    ...normalizeDeviceLogs(deviceId, latestLogText),
  ];

  const screenshotEntries = (artifacts?.screenshots || [])
    .filter((fileName) => !timeline.some((entry) => entry.artifact_path === `screenshots/${fileName}`))
    .map((fileName, index) => ({
      id: `artifact_screenshot_${fileName}_${index}`,
      timestamp: new Date(0).toISOString(),
      device_id: deviceId,
      type: 'artifact_screenshot',
      level: 'info' as LogLevel,
      message: `截图归档: ${fileName}`,
      details: { file_name: fileName },
      screenshot_url: logApi.getArtifactFileUrl(deviceId, `screenshots/${fileName}`),
      artifact_path: `screenshots/${fileName}`,
      download_url: logApi.getArtifactFileUrl(deviceId, `screenshots/${fileName}`),
      source: 'artifacts' as const,
    }));

  return [...timeline, ...screenshotEntries].sort((a, b) => {
    const tsDiff = new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime();
    if (tsDiff !== 0) {
      return tsDiff;
    }
    return a.id.localeCompare(b.id);
  });
}

function getLatestTaskId(logs: LogEntry[]): string | null {
  for (let i = logs.length - 1; i >= 0; i -= 1) {
    if (isNonEmptyString(logs[i].task_id)) {
      return logs[i].task_id || null;
    }
  }
  return null;
}

function sortLogs(logs: LogEntry[]): LogEntry[] {
  return [...logs].sort((a, b) => {
    const tsDiff = new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime();
    if (tsDiff !== 0) {
      return tsDiff;
    }
    return a.id.localeCompare(b.id);
  });
}

export const useLogStore = create<LogState>((set, get) => ({
  logs: {},
  artifacts: {},
  latestTaskIds: {},
  loading: false,
  error: null,

  fetchLogs: async (deviceId: string) => {
    logStoreLogger.info('[fetchLogs] Fetching device archive timeline', { deviceId });
    set({ loading: true, error: null });

    try {
      const [history, artifacts, latestLogText] = await Promise.all([
        logApi.getDeviceHistory(deviceId),
        logApi.getDeviceArtifacts(deviceId),
        logApi.getLatestLogsText(deviceId).catch((error: any) => {
          if (error?.response?.status === 404) {
            return '';
          }
          throw error;
        }),
      ]);

      const mergedLogs = mergeTimeline(deviceId, history, artifacts, latestLogText);
      const latestTaskId = getLatestTaskId(mergedLogs);

      set((state) => ({
        logs: {
          ...state.logs,
          [deviceId]: mergedLogs,
        },
        artifacts: {
          ...state.artifacts,
          [deviceId]: artifacts,
        },
        latestTaskIds: {
          ...state.latestTaskIds,
          [deviceId]: latestTaskId,
        },
        loading: false,
        error: null,
      }));

      logStoreLogger.info('[fetchLogs] Device archive timeline ready', {
        deviceId,
        count: mergedLogs.length,
        latestTaskId,
      });
    } catch (error: any) {
      logStoreLogger.error('[fetchLogs] Failed to fetch device archive timeline', {
        deviceId,
        error: error.message,
      });
      set({ error: error.message || 'Failed to fetch logs', loading: false });
    }
  },

  importLogs: (deviceId, importedLogs) => {
    logStoreLogger.info('[importLogs] Importing local timeline logs', {
      deviceId,
      count: importedLogs.length,
    });

    const existingLogs = get().logs[deviceId] || [];
    const merged = sortLogs([
      ...existingLogs,
      ...importedLogs.map((log, index) => ({
        ...log,
        id: log.id || `imported_${Date.now()}_${index}`,
        device_id: deviceId,
        source: log.source || 'imported',
      })),
    ]);

    set((state) => ({
      logs: {
        ...state.logs,
        [deviceId]: merged,
      },
      latestTaskIds: {
        ...state.latestTaskIds,
        [deviceId]: getLatestTaskId(merged),
      },
    }));
  },

  setLogs: (deviceId, logs) => {
    const sorted = sortLogs(logs);
    set((state) => ({
      logs: {
        ...state.logs,
        [deviceId]: sorted,
      },
      latestTaskIds: {
        ...state.latestTaskIds,
        [deviceId]: getLatestTaskId(sorted),
      },
    }));
  },

  getLogsForDevice: (deviceId) => get().logs[deviceId] || [],
  getArtifactsForDevice: (deviceId) => get().artifacts[deviceId] || null,
  getLatestTaskIdForDevice: (deviceId) => get().latestTaskIds[deviceId] || null,
}));
