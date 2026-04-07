import { create } from 'zustand';
import type { LogEntry, LogLevel } from '../types';
import { logApi } from '../services/api';
import { logStoreLogger } from '../hooks/useLogger';

interface LogState {
  logs: Record<string, LogEntry[]>; // device_id -> logs
  filters: {
    level?: LogLevel;
    type?: string;
    search?: string;
  };
  loading: boolean;
  error: string | null;

  // Actions
  fetchLogs: (deviceId: string, params?: {
    level?: string;
    log_type?: string;
    task_id?: string;
    start_time?: string;
    end_time?: string;
    limit?: number;
    offset?: number;
  }) => Promise<void>;
  addLog: (deviceId: string, log: LogEntry) => void;
  addLogs: (deviceId: string, logs: LogEntry[]) => void;
  clearLogs: (deviceId: string) => Promise<void>;
  setLogs: (deviceId: string, logs: LogEntry[]) => void;
  setFilter: (filter: Partial<LogState['filters']>) => void;

  // Computed
  getLogsForDevice: (deviceId: string) => LogEntry[];
  getFilteredLogs: (deviceId: string) => LogEntry[];
}

export const useLogStore = create<LogState>((set, get) => ({
  logs: {},
  filters: {},
  loading: false,
  error: null,

  fetchLogs: async (deviceId: string, params?: {
    level?: string;
    log_type?: string;
    task_id?: string;
    start_time?: string;
    end_time?: string;
    limit?: number;
    offset?: number;
  }) => {
    logStoreLogger.debug('[fetchLogs] Fetching logs', { deviceId, params });
    set({ loading: true, error: null });
    try {
      const response = await logApi.getDeviceLogs(deviceId, params);
      const logs: LogEntry[] = response.logs.map((log: any) => ({
        id: log.id,
        timestamp: log.created_at,
        device_id: log.device_id,
        task_id: log.task_id,
        type: log.log_type,
        level: log.level,
        message: log.message,
        details: log.details,
        screenshot_url: log.screenshot_url,
      }));
      logStoreLogger.info('[fetchLogs] Logs fetched', { deviceId, count: logs.length });
      set((state) => ({
        logs: {
          ...state.logs,
          [deviceId]: logs,
        },
        loading: false,
      }));
    } catch (error: any) {
      logStoreLogger.error('[fetchLogs] Failed to fetch logs', { deviceId, error: error.message });
      set({ error: error.message || 'Failed to fetch logs', loading: false });
    }
  },

  addLog: (deviceId, log) => {
    logStoreLogger.debug('[addLog] Adding log entry', { deviceId, level: log.level, message: log.message.substring(0, 50) });
    set((state) => ({
      logs: {
        ...state.logs,
        [deviceId]: [...(state.logs[deviceId] || []), log],
      },
    }));
  },

  addLogs: (deviceId, logs) => {
    logStoreLogger.debug('[addLogs] Adding log entries', { deviceId, count: logs.length });
    set((state) => ({
      logs: {
        ...state.logs,
        [deviceId]: [...(state.logs[deviceId] || []), ...logs],
      },
    }));
  },

  clearLogs: async (deviceId: string) => {
    logStoreLogger.info('[clearLogs] Clearing logs', { deviceId });
    try {
      await logApi.clear(deviceId);
      set((state) => ({
        logs: {
          ...state.logs,
          [deviceId]: [],
        },
      }));
    } catch (error: any) {
      logStoreLogger.error('[clearLogs] Failed to clear logs', { deviceId, error: error.message });
      set({ error: error.message });
    }
  },

  setLogs: (deviceId, logs) => {
    logStoreLogger.debug('[setLogs] Setting logs', { deviceId, count: logs.length });
    set((state) => ({
      logs: {
        ...state.logs,
        [deviceId]: logs,
      },
    }));
  },

  setFilter: (filter) => {
    logStoreLogger.debug('[setFilter] Filter updated', filter);
    set((state) => ({
      filters: { ...state.filters, ...filter },
    }));
  },

  getLogsForDevice: (deviceId) => {
    return get().logs[deviceId] || [];
  },

  getFilteredLogs: (deviceId) => {
    const { logs, filters } = get();
    let result = logs[deviceId] || [];

    if (filters.level) {
      result = result.filter((log) => log.level === filters.level);
    }

    if (filters.type) {
      result = result.filter((log) => log.type === filters.type);
    }

    if (filters.search) {
      const search = filters.search.toLowerCase();
      result = result.filter((log) =>
        log.message.toLowerCase().includes(search)
      );
    }

    return result;
  },
}));
