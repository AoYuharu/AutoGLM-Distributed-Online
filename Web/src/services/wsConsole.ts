/**
 * WebSocket Console Service
 *
 * Handles WebSocket connection from Web to Server for receiving real-time updates:
 * - agent_step: Agent step execution results
 * - agent_status: Agent status changes
 * - agent_action_pending: Actions requiring user confirmation
 * - session_locked: Main control session acquired
 * - session_released: Main control session released
 * - batch_step_update: Batch task step updates
 * - batch_device_finished: Batch task device completion
 * - device_sync: Device status sync from server
 */

import { wsConsoleLogger } from '../hooks/useLogger';
import { useDeviceStore } from '../stores/deviceStore';

// Message types from Server -> Web
export interface WsConsoleMessage {
  type:
    | 'agent_step'
    | 'agent_status'
    | 'agent_action_pending'
    | 'session_locked'
    | 'session_released'
    | 'batch_step_update'
    | 'batch_device_finished'
    | 'connected'
    | 'subscribed'
    | 'error'
    | 'device_sync'
    | 'task_created'
    | 'task_interrupted'
    | 'agent_phase_start'
    | 'agent_phase_end'
    | 'agent_thinking'
    | 'phase_confirmed';
  task_id?: string;
  device_id?: string;
  step_number?: number;
  max_steps?: number;
  action?: Record<string, any>;
  reasoning?: string;
  result?: string;
  screenshot?: string;
  success?: boolean;
  error?: string;
  status?: string;
  message?: string;
  controller_id?: string;
  data?: Record<string, any>;
  devices?: Array<{
    device_id: string;
    status: string;
    last_update: string;
  }>;
  phase?: 'reason' | 'act' | 'observe';
  thinking?: string;
}

// Message types from Web -> Server
export interface WsConsoleSendMessage {
  type: 'subscribe' | 'unsubscribe' | 'sync' | 'confirm_phase' | 'create_task' | 'interrupt_task';
  device_id?: string;
  approved?: boolean;
  instruction?: string;
  mode?: string;
  max_steps?: number;
  task_id?: string;
}

type MessageCallback = (message: WsConsoleMessage) => void;

class WsConsoleService {
  private ws: WebSocket | null = null;
  private consoleId: string = '';
  private reconnectAttempts: number = 0;
  private maxReconnectAttempts: number = 10;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private subscriptions: Set<string> = new Set();
  private callbacks: Set<MessageCallback> = new Set();
  private isIntentionallyDisconnected: boolean = false;

  // Get WebSocket URL based on current location
  private getWsUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    // Use relative path to Server
    const serverHost = import.meta.env.VITE_API_URL?.replace(/^http:\/\//, '') || host;
    return `${protocol}//${serverHost}/ws/console?console_id=${this.consoleId}`;
  }

  /**
   * Generate a unique console ID
   */
  private generateConsoleId(): string {
    return `console_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  /**
   * Connect to the WebSocket server
   */
  connect(onMessage?: MessageCallback): void {
    if (onMessage) {
      this.callbacks.add(onMessage);
    }

    if (this.ws?.readyState === WebSocket.OPEN) {
      wsConsoleLogger.debug('[connect] Already connected');
      return;
    }

    if (this.ws?.readyState === WebSocket.CONNECTING) {
      wsConsoleLogger.debug('[connect] Connection already in progress', { consoleId: this.consoleId });
      return;
    }

    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }

    if (!this.consoleId) {
      this.consoleId = this.generateConsoleId();
    }
    this.isIntentionallyDisconnected = false;

    const wsUrl = this.getWsUrl();
    wsConsoleLogger.info('[connect] Connecting to WebSocket server', { url: wsUrl, consoleId: this.consoleId });

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        wsConsoleLogger.info('[connect] WebSocket connected successfully', { consoleId: this.consoleId });
        this.reconnectAttempts = 0;
        this.isIntentionallyDisconnected = false;

        // Resubscribe to previously subscribed devices
        this.resubscribeAll();
      };

      this.ws.onclose = (event) => {
        wsConsoleLogger.warn('[connect] WebSocket disconnected', {
          consoleId: this.consoleId,
          code: event.code,
          reason: event.reason,
        });

        this.ws = null;

        // Auto-reconnect if not intentionally disconnected
        if (!this.isIntentionallyDisconnected && this.reconnectAttempts < this.maxReconnectAttempts) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = (error) => {
        wsConsoleLogger.error('[connect] WebSocket error', { error: String(error) });
      };

      this.ws.onmessage = (event) => {
        this.handleMessage(event);
      };
    } catch (error) {
      wsConsoleLogger.error('[connect] Failed to create WebSocket', { error: String(error) });
      this.scheduleReconnect();
    }
  }

  /**
   * Handle incoming WebSocket message
   */
  private handleMessage(event: MessageEvent): void {
    try {
      const message: WsConsoleMessage = JSON.parse(event.data);
      const msgType = message.type || 'unknown';

      wsConsoleLogger.debug('[handleMessage] Received message', { type: msgType, consoleId: this.consoleId });

      // Log detailed message content for specific types
      switch (message.type) {
        case 'agent_step':
          wsConsoleLogger.info('[handleMessage] agent_step received', {
            taskId: message.task_id,
            deviceId: message.device_id,
            stepNumber: message.step_number,
            success: message.success,
          });
          break;
        case 'agent_status':
          wsConsoleLogger.info('[handleMessage] agent_status received', {
            deviceId: message.device_id,
            status: message.status,
          });
          break;
        case 'session_locked':
          wsConsoleLogger.info('[handleMessage] session_locked received', {
            deviceId: message.device_id,
            controllerId: message.controller_id,
          });
          break;
        case 'session_released':
          wsConsoleLogger.info('[handleMessage] session_released received', {
            deviceId: message.device_id,
          });
          break;
        case 'device_sync':
          wsConsoleLogger.info('[handleMessage] device_sync received', {
            deviceCount: message.devices?.length || 0,
          });
          // Update all devices from the sync message
          if (message.devices && Array.isArray(message.devices)) {
            message.devices.forEach((deviceData: { device_id: string; status: string; last_update?: string }) => {
              useDeviceStore.getState().updateDevice(deviceData.device_id, {
                status: deviceData.status as any,
                last_seen: deviceData.last_update,
              });
            });
            wsConsoleLogger.info('[handleMessage] device_sync applied', {
              devices: message.devices.map((d: { device_id: string; status: string }) => `${d.device_id}:${d.status}`).join(', '),
            });
          }
          break;
      }

      // Notify all callbacks
      this.callbacks.forEach((callback) => {
        try {
          callback(message);
        } catch (error) {
          wsConsoleLogger.error('[handleMessage] Callback error', { error: String(error) });
        }
      });
    } catch (error) {
      wsConsoleLogger.error('[handleMessage] Failed to parse message', { error: String(error) });
    }
  }

  /**
   * Schedule reconnection attempt
   */
  private scheduleReconnect(): void {
    if (this.isIntentionallyDisconnected) {
      return;
    }

    this.reconnectAttempts++;
    const baseDelay = 1000; // 1 second
    const maxDelay = 30000; // 30 seconds
    const delay = Math.min(baseDelay * Math.pow(2, this.reconnectAttempts - 1), maxDelay);
    const jitter = Math.random() * 1000;

    wsConsoleLogger.info('[scheduleReconnect] Scheduling reconnect', {
      attempt: this.reconnectAttempts,
      delayMs: delay + jitter,
      maxAttempts: this.maxReconnectAttempts,
    });

    this.reconnectTimeout = setTimeout(() => {
      this.connect();
    }, delay + jitter);
  }

  /**
   * Resubscribe to all previously subscribed devices
   */
  private resubscribeAll(): void {
    this.subscriptions.forEach((deviceId) => {
      this.sendSubscribe(deviceId);
    });
  }

  /**
   * Send subscribe message for a device
   */
  private sendSubscribe(deviceId: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      const message: WsConsoleSendMessage = { type: 'subscribe', device_id: deviceId };
      this.ws.send(JSON.stringify(message));
      wsConsoleLogger.debug('[sendSubscribe] Subscribed to device', { deviceId, consoleId: this.consoleId });
    }
  }

  /**
   * Disconnect from the WebSocket server
   */
  disconnect(): void {
    wsConsoleLogger.info('[disconnect] Intentionally disconnecting', { consoleId: this.consoleId });

    this.isIntentionallyDisconnected = true;

    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    this.consoleId = '';
    this.subscriptions.clear();
    this.callbacks.clear();
  }

  /**
   * Subscribe to a device's updates
   */
  subscribe(deviceId: string): void {
    if (this.subscriptions.has(deviceId)) {
      wsConsoleLogger.debug('[subscribe] Already subscribed', { deviceId });
      return;
    }

    this.subscriptions.add(deviceId);
    this.sendSubscribe(deviceId);
  }

  /**
   * Unsubscribe from a device's updates
   */
  unsubscribe(deviceId: string): void {
    if (!this.subscriptions.has(deviceId)) {
      wsConsoleLogger.debug('[unsubscribe] Not subscribed', { deviceId });
      return;
    }

    this.subscriptions.delete(deviceId);

    if (this.ws?.readyState === WebSocket.OPEN) {
      const message: WsConsoleSendMessage = { type: 'unsubscribe', device_id: deviceId };
      this.ws.send(JSON.stringify(message));
      wsConsoleLogger.debug('[unsubscribe] Unsubscribed from device', { deviceId });
    }
  }

  /**
   * Request device status synchronization from server
   */
  sync(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      const message: WsConsoleSendMessage = { type: 'sync' };
      this.ws.send(JSON.stringify(message));
      wsConsoleLogger.debug('[sync] Sync request sent', { consoleId: this.consoleId });
    } else {
      wsConsoleLogger.warn('[sync] Cannot send sync request, not connected');
    }
  }

  /**
   * Send phase confirmation to server
   */
  sendConfirmPhase(deviceId: string, approved: boolean): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      const message: WsConsoleSendMessage = { type: 'confirm_phase', device_id: deviceId, approved };
      this.ws.send(JSON.stringify(message));
      wsConsoleLogger.debug('[sendConfirmPhase] Phase confirmation sent', { deviceId, approved, consoleId: this.consoleId });
    } else {
      wsConsoleLogger.warn('[sendConfirmPhase] Cannot send confirm phase, not connected');
    }
  }

  /**
   * Send create_task message to server
   */
  sendCreateTask(deviceId: string, instruction: string, mode: string = 'normal', maxSteps: number = 100): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      const message: WsConsoleSendMessage = {
        type: 'create_task',
        device_id: deviceId,
        instruction,
        mode,
        max_steps: maxSteps,
      };
      this.ws.send(JSON.stringify(message));
      wsConsoleLogger.debug('[sendCreateTask] Create task sent', { deviceId, instruction: instruction.substring(0, 50), consoleId: this.consoleId });
    } else {
      wsConsoleLogger.warn('[sendCreateTask] Cannot send create task, not connected');
    }
  }

  /**
   * Send interrupt_task message to server
   */
  sendInterruptTask(deviceId: string, taskId: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      const message: WsConsoleSendMessage = {
        type: 'interrupt_task',
        device_id: deviceId,
        task_id: taskId,
      };
      this.ws.send(JSON.stringify(message));
      wsConsoleLogger.debug('[sendInterruptTask] Interrupt task sent', { deviceId, taskId, consoleId: this.consoleId });
    } else {
      wsConsoleLogger.warn('[sendInterruptTask] Cannot send interrupt task, not connected');
    }
  }

  /**
   * Add a message callback
   */
  addCallback(callback: MessageCallback): void {
    this.callbacks.add(callback);
  }

  /**
   * Remove a message callback
   */
  removeCallback(callback: MessageCallback): void {
    this.callbacks.delete(callback);
  }

  /**
   * Check if connected
   */
  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  /**
   * Get current console ID
   */
  getConsoleId(): string {
    return this.consoleId;
  }

  /**
   * Get list of subscribed device IDs
   */
  getSubscriptions(): string[] {
    return Array.from(this.subscriptions);
  }
}

// Singleton instance
export const wsConsoleApi = new WsConsoleService();
