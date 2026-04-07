import { useEffect, useRef, useCallback } from 'react';
import { useAppStore } from '../stores/appStore';
import { useAgentStore } from '../stores/agentStore';
import { useDeviceStore } from '../stores/deviceStore';
import { wsLogger } from './useLogger';

interface WebSocketConfig {
  url: string;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (error: Event) => void;
  onMessage?: (message: any) => void;
}

export function useWebSocket(config: WebSocketConfig) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttempts = useRef(0);

  const { setWsConnected } = useAppStore();
  const { addAgentMessage } = useAgentStore();
  const { updateDevice, setDeviceOffline } = useDeviceStore();

  const maxReconnectAttempts = 10;
  const baseDelay = 1000; // 1秒
  const maxDelay = 30000; // 30秒

  const getReconnectDelay = useCallback(() => {
    const delay = Math.min(
      baseDelay * Math.pow(2, reconnectAttempts.current),
      maxDelay
    );
    // 添加 jitter
    return delay + Math.random() * 1000;
  }, []);

  const handleMessage = useCallback((event: MessageEvent) => {
    try {
      const message = JSON.parse(event.data);
      const msgType = message.type || 'unknown';

      wsLogger.debug(`[handleMessage] Received message: ${msgType}`, { msgType });

      switch (message.type) {
        case 'device_status_update':
          // 更新设备状态
          wsLogger.info(`[handleMessage] Device status update: ${message.device_id}`, {
            deviceId: message.device_id,
            status: message.update?.status,
          });
          updateDevice(message.device_id, message.update);
          break;

        case 'device_offline':
          // 设备离线，禁用控件
          wsLogger.warn(`[handleMessage] Device offline: ${message.device_id}`, { deviceId: message.device_id });
          setDeviceOffline(message.device_id);
          addAgentMessage(`设备 ${message.device_id} 已离线`);
          break;

        case 'device_reconnected':
          // 设备重连，恢复控件
          wsLogger.info(`[handleMessage] Device reconnected: ${message.device_id}`, { deviceId: message.device_id });
          updateDevice(message.device_id, { status: 'idle' });
          addAgentMessage(`设备 ${message.device_id} 已重连`);
          break;

        case 'task_update':
          // 任务状态更新
          wsLogger.debug(`[handleMessage] Task update: ${message.payload?.task_id}`, {
            taskId: message.payload?.task_id,
            status: message.payload?.status,
          });
          if (message.payload) {
            updateDevice(message.device_id, {
              current_task_id: message.payload.task_id,
              status: message.payload.status
            });
          }
          break;

        case 'agent_status':
          // Agent状态更新
          wsLogger.debug(`[handleMessage] Agent status update`, {
            sessionId: message.data?.session_id,
          });
          if (message.data) {
            addAgentMessage(`${message.data.session_id}: ${message.data.message}`);
          }
          break;

        case 'pong':
          // 心跳响应，忽略
          wsLogger.debug('[handleMessage] Pong received');
          break;

        default:
          wsLogger.debug(`[handleMessage] Passing to custom handler: ${msgType}`);
          config.onMessage?.(message);
      }
    } catch (error) {
      wsLogger.error('[handleMessage] Failed to parse message', { error: String(error) });
      console.error('Failed to parse WebSocket message:', error);
    }
  }, [updateDevice, setDeviceOffline, addAgentMessage, config]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsLogger.debug('[connect] Already connected');
      return;
    }

    wsLogger.info(`[connect] Connecting to ${config.url}, attempt ${reconnectAttempts.current + 1}/${maxReconnectAttempts}`);

    const ws = new WebSocket(config.url);
    wsRef.current = ws;

    ws.onopen = () => {
      wsLogger.info('[connect] WebSocket connected successfully');
      setWsConnected(true);
      reconnectAttempts.current = 0;
      config.onOpen?.();
    };

    ws.onclose = () => {
      wsLogger.warn('[connect] WebSocket disconnected');
      setWsConnected(false);
      wsRef.current = null;
      config.onClose?.();

      // 自动重连
      if (reconnectAttempts.current < maxReconnectAttempts) {
        const delay = getReconnectDelay();
        wsLogger.info(`[connect] Reconnecting in ${delay}ms... (attempt ${reconnectAttempts.current + 1})`);
        reconnectTimeoutRef.current = setTimeout(() => {
          reconnectAttempts.current++;
          connect();
        }, delay);
      } else {
        wsLogger.error('[connect] Max reconnect attempts reached, giving up');
      }
    };

    ws.onerror = (error) => {
      wsLogger.error('[connect] WebSocket error', { error: String(error) });
      config.onError?.(error);
    };

    ws.onmessage = handleMessage;
  }, [config, setWsConnected, handleMessage, getReconnectDelay]);

  const disconnect = useCallback(() => {
    wsLogger.info('[disconnect] Manually disconnecting');
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  return { connect, disconnect, send: (data: any) => wsRef.current?.send(JSON.stringify(data)) };
}
