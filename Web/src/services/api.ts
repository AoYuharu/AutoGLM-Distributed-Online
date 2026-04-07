/**
 * API Service - connects frontend to backend
 */
import axios from 'axios';

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
  register: async (deviceId: string, data: {
    platform: string;
    model?: string;
    os_version?: string;
    screen_width?: number;
    screen_height?: number;
    capabilities?: Record<string, any>;
  }) => {
    const response = await api.post(`/api/v1/devices/${deviceId}/register`, {
      device_id: deviceId,
      ...data,
    });
    return response.data;
  },

  /** Device heartbeat */
  heartbeat: async (deviceId: string) => {
    const response = await api.post(`/api/v1/devices/${deviceId}/heartbeat`);
    return response.data;
  },

  /** Update device status */
  updateStatus: async (deviceId: string, data: {
    status: string;
    device_info?: {
      model?: string;
      os_version?: string;
      screen_width?: number;
      screen_height?: number;
    };
    current_task_id?: string;
  }) => {
    const response = await api.post(`/api/v1/devices/${deviceId}/status`, data);
    return response.data;
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
  /** Create a new task */
  create: async (data: {
    device_id?: string;
    platform?: string;
    instruction: string;
    mode?: 'normal' | 'cautious';
    max_steps?: number;
    priority?: number;
  }) => {
    const response = await api.post('/api/v1/tasks', data);
    return response.data;
  },

  /** Create batch tasks */
  createBatch: async (data: {
    dispatch_mode: 'parallel' | 'sequential';
    tasks: Array<{
      device_id?: string;
      platform?: string;
      instruction: string;
      mode?: 'normal' | 'cautious';
    }>;
  }) => {
    const response = await api.post('/api/v1/tasks/batch', data);
    return response.data;
  },

  /** Get all tasks */
  list: async (params?: { status?: string; device_id?: string; limit?: number; offset?: number }) => {
    const response = await api.get('/api/v1/tasks', { params });
    return response.data;
  },

  /** Get single task with steps */
  get: async (taskId: string) => {
    const response = await api.get(`/api/v1/tasks/${taskId}`);
    return response.data;
  },

  /** Get task steps */
  getSteps: async (taskId: string) => {
    const response = await api.get(`/api/v1/tasks/${taskId}/steps`);
    return response.data;
  },

  /** Interrupt a task */
  interrupt: async (taskId: string) => {
    const response = await api.post(`/api/v1/tasks/${taskId}/interrupt`);
    return response.data;
  },

  /** Update task progress */
  updateProgress: async (taskId: string, data: {
    current_step: number;
    status: string;
    result?: Record<string, any>;
  }) => {
    const response = await api.post(`/api/v1/tasks/${taskId}/update`, data);
    return response.data;
  },

  /** Add task step */
  addStep: async (taskId: string, data: {
    step_number: number;
    action_type: string;
    action_params: Record<string, any>;
    thinking?: string;
    duration_ms?: number;
    success?: boolean;
    error?: string;
    screenshot_url?: string;
  }) => {
    const response = await api.post(`/api/v1/tasks/${taskId}/steps`, data);
    return response.data;
  },

  /** Submit action decision in cautious mode */
  submitDecision: async (taskId: string, stepId: string, data: {
    action: 'confirm' | 'reject' | 'skip';
    reason?: string;
  }) => {
    const response = await api.post(`/api/v1/tasks/${taskId}/steps/${stepId}/decision`, data);
    return response.data;
  },

  /** Delete task */
  delete: async (taskId: string) => {
    const response = await api.delete(`/api/v1/tasks/${taskId}`);
    return response.data;
  },
};

// ============= Log APIs =============

export const logApi = {
  /** Get logs for a device */
  getDeviceLogs: async (deviceId: string, params?: {
    level?: string;
    log_type?: string;
    task_id?: string;
    start_time?: string;
    end_time?: string;
    limit?: number;
    offset?: number;
  }) => {
    const response = await api.get(`/api/v1/logs/${deviceId}`, { params });
    return response.data;
  },

  /** Upload logs */
  uploadLogs: async (deviceId: string, data: {
    logs: Array<{
      timestamp: string;
      log_type: string;
      level: string;
      message: string;
      details?: Record<string, any>;
      screenshot_url?: string;
    }>;
    client_info?: Record<string, string>;
  }) => {
    const response = await api.post(`/api/v1/logs/${deviceId}/upload`, data);
    return response.data;
  },

  /** Create single log entry */
  create: async (deviceId: string, data: {
    timestamp: string;
    log_type: string;
    level: string;
    message: string;
    details?: Record<string, any>;
    screenshot_url?: string;
  }) => {
    const response = await api.post(`/api/v1/logs/${deviceId}`, data);
    return response.data;
  },

  /** Clear device logs */
  clear: async (deviceId: string) => {
    const response = await api.delete(`/api/v1/logs/${deviceId}`);
    return response.data;
  },
};

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
