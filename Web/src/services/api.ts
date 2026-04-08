/**
 * API Service - connects frontend to backend
 */
import axios from 'axios';
import type { DeviceArtifacts } from '../types';

declare module 'axios' {
  interface InternalAxiosRequestConfig {
    metadata?: {
      startTime?: number;
    };
  }
}

// Use relative path to go through Vite proxy in dev, fallback to localhost:8000 in prod
const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor for logging
api.interceptors.request.use(
  (config) => {
    const startTime = Date.now();
    config.metadata = { startTime };
    console.log(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    return config;
  },
  (error) => {
    console.error('[API] Request error:', error.message);
    return Promise.reject(error);
  }
);

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => {
    const duration = response.config.metadata?.startTime
      ? `${Date.now() - response.config.metadata.startTime}ms`
      : 'unknown';
    console.log(`[API] ${response.config.method?.toUpperCase()} ${response.config.url} - ${response.status} (${duration})`);
    return response;
  },
  (error) => {
    const duration = error.config?.metadata?.startTime
      ? `${Date.now() - error.config.metadata.startTime}ms`
      : 'unknown';
    console.error(`[API Error] ${error.config?.method?.toUpperCase()} ${error.config?.url} - ${error.response?.status || 'network'} (${duration})`);
    return Promise.reject(error);
  }
);

// ============= Device APIs =============

export const deviceApi = {
  /** Get all devices */
  list: async (params?: { platform?: string; status?: string }) => {
    const response = await api.get('/api/v1/devices', { params });
    return response.data;
  },

  /** Get single device */
  get: async (deviceId: string) => {
    const response = await api.get(`/api/v1/devices/${deviceId}`);
    return response.data;
  },

  /** Register device */
  // NOTE: No such endpoint on server — client registration happens via WebSocket /status.
  register: async (_deviceId: string, _data: {
    platform: string;
    model?: string;
    os_version?: string;
    screen_width?: number;
    screen_height?: number;
    capabilities?: Record<string, any>;
  }) => {
    console.warn('[deviceApi.register] Endpoint not implemented on server');
    throw new Error('deviceApi.register is not implemented');
  },

  /** Device heartbeat */
  // NOTE: No such endpoint on server — heartbeats go through WebSocket.
  heartbeat: async (_deviceId: string) => {
    console.warn('[deviceApi.heartbeat] Endpoint not implemented on server');
    throw new Error('deviceApi.heartbeat is not implemented');
  },

  /** Update device status */
  // NOTE: Server uses POST /api/v1/devices/status (device_id in body), not /devices/{id}/status.
  // This endpoint is not called from UI; stubbed to prevent accidental use.
  updateStatus: async (_deviceId: string, _data: {
    status: string;
    device_info?: {
      model?: string;
      os_version?: string;
      screen_width?: number;
      screen_height?: number;
    };
    current_task_id?: string;
  }) => {
    console.warn('[deviceApi.updateStatus] Endpoint not implemented on server (device_id in body, not path)');
    throw new Error('deviceApi.updateStatus is not implemented — use WebSocket for status updates');
  },

  /** Update device remark */
  updateRemark: async (deviceId: string, remark: string) => {
    const response = await api.patch(`/api/v1/devices/${deviceId}/remark`, { remark });
    return response.data;
  },

  /** Delete device */
  delete: async (deviceId: string) => {
    const response = await api.delete(`/api/v1/devices/${deviceId}`);
    return response.data;
  },
};

// ============= Task APIs =============

export const taskApi = {
  /** Get all tasks (now redirects to device sessions) */
  list: async (params?: { status?: string; device_id?: string; limit?: number; offset?: number }) => {
    // Task list is now per-device via getSession
    const response = await api.get('/api/v1/devices', { params });
    return response.data;
  },

  /** Get single task with steps (now redirects to device history) */
  get: async (_taskId: string) => {
    // taskId is no longer used directly - device history is used instead
    const response = await api.get(`/api/v1/devices`);
    return response.data;
  },

  /** Get task steps (now redirects to device history) */
  getSteps: async (_taskId: string) => {
    // taskId is no longer used directly
    const response = await api.get(`/api/v1/devices`);
    return response.data;
  },

  /** Interrupt a task (now redirects to device interrupt endpoint) */
  interrupt: async (_taskId: string) => {
    // taskId is no longer used directly - use device_id from context
    const response = await api.post(`/api/v1/devices`);
    return response.data;
  },
};

// ============= Log / Artifact APIs =============

export interface DeviceHistoryResponse {
  device_id: string;
  react_records: Array<Record<string, any>>;
  chat_history: Array<Record<string, any>>;
  screenshots: string[];
}

export const logApi = {
  getDeviceHistory: async (deviceId: string): Promise<DeviceHistoryResponse> => {
    const response = await api.get(`/api/v1/devices/${deviceId}/history`);
    return response.data;
  },

  getDeviceArtifacts: async (deviceId: string): Promise<DeviceArtifacts> => {
    const response = await api.get(`/api/v1/devices/${deviceId}/artifacts`);
    return response.data;
  },

  getLatestLogsText: async (deviceId: string): Promise<string> => {
    const response = await api.get(`/api/v1/devices/${deviceId}/artifacts/logs/latest`, {
      responseType: 'text',
      transformResponse: [(data) => data],
    });
    return typeof response.data === 'string' ? response.data : '';
  },

  getArtifactFileUrl: (deviceId: string, path: string): string => (
    `/api/v1/devices/${deviceId}/artifacts/file?path=${encodeURIComponent(path)}`
  ),

  getRawDownloadUrl: (downloadPath: string | null | undefined): string | null => {
    if (!downloadPath) {
      return null;
    }
    return downloadPath.startsWith('/') ? downloadPath : `/${downloadPath}`;
  },
};

export { api };

// ============= Client APIs =============

export const clientApi = {
  /** Create a new client */
  create: async (data: { name: string }) => {
    const response = await api.post('/api/v1/clients', data);
    return response.data;
  },

  /** Get all clients */
  list: async () => {
    const response = await api.get('/api/v1/clients');
    return response.data;
  },

  /** Get single client */
  get: async (clientId: string) => {
    const response = await api.get(`/api/v1/clients/${clientId}`);
    return response.data;
  },

  /** Verify API key */
  verifyKey: async (apiKey: string) => {
    const response = await api.post('/api/v1/clients/verify', null, {
      params: { api_key: apiKey },
    });
    return response.data;
  },

  /** Delete client */
  delete: async (clientId: string) => {
    const response = await api.delete(`/api/v1/clients/${clientId}`);
    return response.data;
  },
};

// ============= Health Check =============

export const healthApi = {
  check: async () => {
    const response = await api.get('/health');
    return response.data;
  },
};

// ============= Pending Device APIs =============

export const pendingDeviceApi = {
  /** Get all pending devices */
  list: async () => {
    const response = await api.get('/api/v1/devices/pending/list');
    return response.data;
  },

  /** Approve pending device */
  approve: async (deviceId: string) => {
    const response = await api.post(`/api/v1/devices/pending/${deviceId}/approve`);
    return response.data;
  },

  /** Reject pending device */
  reject: async (deviceId: string, reason?: string) => {
    const response = await api.post(`/api/v1/devices/pending/${deviceId}/reject`, {
      reason,
    });
    return response.data;
  },
};

export default api;
