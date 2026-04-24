/**
 * Mock Server - HTTP + WebSocket server for Playwright E2E tests
 * 完整模拟后端服务器，支持所有 WebSocket 消息交互
 */

const fs = require('fs');
const path = require('path');
const http = require('http');
const { WebSocketServer } = require('ws');
const url = require('url');
const { parse } = require('yaml');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SHARED_CONFIG_PATH = path.resolve(REPO_ROOT, 'config', 'server-web.yaml');
const DEFAULT_PORT = 8888;
const WS_CONSOLE_PATH = '/ws/console';

function asRecord(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function readString(value, fallback) {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readNumber(value, fallback) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function loadMockServerConfig() {
  if (!fs.existsSync(SHARED_CONFIG_PATH)) {
    return {
      host: 'localhost',
      port: DEFAULT_PORT,
    };
  }

  try {
    const raw = fs.readFileSync(SHARED_CONFIG_PATH, 'utf-8');
    const data = asRecord(parse(raw) ?? {});
    const web = asRecord(data.web);
    const mockServer = asRecord(web.mock_server);
    return {
      host: readString(mockServer.host, 'localhost'),
      port: readNumber(mockServer.port, DEFAULT_PORT),
    };
  } catch {
    return {
      host: 'localhost',
      port: DEFAULT_PORT,
    };
  }
}

const mockServerConfig = loadMockServerConfig();
const PORT = mockServerConfig.port;

// Store received messages for verification
const serverState = {
  receivedMessages: [],
  confirmedDevices: new Set(),
  createdTasks: [],
  interruptedTasks: [],
};

// Connected WebSocket clients
const clients = new Map();

// Create HTTP server
const httpServer = http.createServer((req, res) => {
  const parsedUrl = url.parse(req.url, true);
  const pathname = parsedUrl.pathname;

  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  // Health check
  if (pathname === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'healthy' }));
    return;
  }

  // GET /api/v1/devices - 返回设备列表
  if (pathname === '/api/v1/devices' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      devices: [
        {
          device_id: 'test-device-001',
          device_name: '测试手机1',
          status: 'idle',
          platform: 'android',
          model: 'Test Phone',
          os_version: '14',
          current_task_id: null,
        },
        {
          device_id: 'test-device-002',
          device_name: '测试手机2',
          status: 'busy',
          platform: 'android',
          model: 'Test Phone 2',
          os_version: '13',
          current_task_id: 'task-busy-001',
        },
      ],
    }));
    return;
  }

  // GET /api/v1/devices/:deviceId/session - 返回会话快照
  const sessionMatch = pathname.match(/^\/api\/v1\/devices\/([^/]+)\/session$/);
  if (sessionMatch && req.method === 'GET') {
    const deviceId = sessionMatch[1];
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      task_id: null,
      instruction: null,
      mode: 'normal',
      status: 'idle',
      current_step: 0,
      max_steps: 100,
      latest_screenshot: null,
      interruptible: false,
    }));
    return;
  }

  // GET /api/v1/devices/:deviceId/chat - 返回聊天历史
  const chatGetMatch = pathname.match(/^\/api\/v1\/devices\/([^/]+)\/chat$/);
  if (chatGetMatch && req.method === 'GET') {
    const deviceId = chatGetMatch[1];
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      device_id: deviceId,
      messages: [],
      total: 0,
    }));
    return;
  }

  // POST /api/v1/devices - 中断设备
  if (pathname === '/api/v1/devices' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      serverState.receivedMessages.push({ type: 'interrupt', body: JSON.parse(body || '{}') });
      serverState.interruptedTasks.push(body);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ success: true }));
    });
    return;
  }

  // 404 for other routes
  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: 'Not found' }));
});

// WebSocket server
const wss = new WebSocketServer({ server: httpServer });

// Send message to specific client
function sendToClient(clientId, message) {
  const client = clients.get(clientId);
  if (client && client.readyState === 1) { // OPEN
    client.send(JSON.stringify(message));
  }
}

// Simulate agent responses
function simulateAgentResponse(deviceId, message, ws) {
  const msgType = message.type;

  switch (msgType) {
    case 'create_task': {
      const taskId = `task_${Date.now()}`;

      // 延迟发送任务创建成功消息
      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'task_created',
          device_id: deviceId,
          task_id: taskId,
          status: 'running',
        }));
      }, 100);

      // Bootstrap screenshot stages
      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 0,
          phase: 'observe',
          stage: 'requesting_initial_screenshot',
          message: '初始截图请求',
        }));
      }, 150);

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 0,
          phase: 'observe',
          stage: 'initial_screenshot_ack_received',
          message: '已获取到ACK，等待初始截图',
        }));
      }, 250);

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 0,
          phase: 'observe',
          stage: 'initial_screenshot_received',
          message: '已获取到初始截图,调用接口',
          screenshot: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0k0AAAAASUVORK5CYII=',
          success: true,
        }));
      }, 350);

      // Reason stages: reason_start + reason_complete (same bubble key)
      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 1,
          phase: 'reason',
          stage: 'reason_start',
          message: '开始调用模型',
        }));
      }, 450);

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 1,
          phase: 'reason',
          stage: 'reason_complete',
          message: 'Reason 完成，动作已解析',
          reasoning: '让我来分析一下用户的需求...',
          action: { action: 'Tap', x: 500, y: 600 },
        }));
      }, 600);

      // Transport stages: no reasoning/action payload
      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 1,
          phase: 'act',
          stage: 'action_dispatched',
          message: '动作已下发',
          reasoning: '让我来分析一下用户的需求...',
          action: { action: 'Tap', x: 500, y: 600 },
        }));
      }, 700);

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 1,
          phase: 'act',
          stage: 'waiting_ack',
          message: '已下发，等待 ACK',
        }));
      }, 800);

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 1,
          phase: 'act',
          stage: 'ack_received',
          message: 'ACK 已收到',
          success: true,
        }));
      }, 900);

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 1,
          phase: 'observe',
          stage: 'waiting_observe',
          message: '已收到ACK，等待observe',
        }));
      }, 1000);

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_progress',
          device_id: deviceId,
          task_id: taskId,
          step_number: 1,
          phase: 'observe',
          stage: 'observe_received',
          message: '已获取到observe图片',
          result: 'ActionResult(success=True)',
          screenshot: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0k0AAAAASUVORK5CYII=',
          success: true,
        }));
      }, 1150);

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_step',
          device_id: deviceId,
          task_id: taskId,
          step_number: 1,
          action: { action: 'Tap', x: 500, y: 600 },
          reasoning: '让我来分析一下用户的需求...',
          result: 'ActionResult(success=True)',
          screenshot: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0k0AAAAASUVORK5CYII=',
          success: true,
        }));
      }, 1150);

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'agent_status',
          device_id: deviceId,
          task_id: taskId,
          status: 'completed',
          message: '任务已完成',
          data: { task_id: taskId },
        }));
      }, 1300);
      break;
    }

    case 'confirm_phase':
      serverState.confirmedDevices.add(deviceId);
      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'phase_confirmed',
          device_id: deviceId,
          approved: Boolean(message.approved),
        }));
      }, 100);

      if (message.approved) {
        setTimeout(() => {
          ws.send(JSON.stringify({
            type: 'agent_progress',
            device_id: deviceId,
            task_id: message.task_id || 'task_confirmed',
            step_number: 2,
            phase: 'act',
            stage: 'action_dispatched',
            message: '动作已下发',
            action: { action: 'Swipe', start_x: 500, start_y: 1000, end_x: 500, end_y: 500 },
          }));
        }, 300);
      }
      break;

    case 'interrupt_task':
      serverState.interruptedTasks.push(deviceId);
      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'task_interrupted',
          device_id: deviceId,
          task_id: message.task_id,
          success: true,
        }));
      }, 100);
      break;
  }
}

wss.on('connection', (ws, req) => {
  const parsedUrl = url.parse(req.url, true);
  const pathname = parsedUrl.path;
  const consoleId = parsedUrl.query?.console_id || 'default';

  console.log(`[Mock WS] Client connected: ${pathname}, console_id: ${consoleId}`);

  // 存储客户端连接
  clients.set(consoleId, ws);

  // 发送连接成功消息
  ws.send(JSON.stringify({
    type: 'connected',
    message: 'Connected to mock server',
    console_id: consoleId,
    timestamp: new Date().toISOString(),
  }));

  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data.toString());
      serverState.receivedMessages.push(msg);
      console.log('[Mock WS] Received:', JSON.stringify(msg));

      // 处理不同类型的消息
      const deviceId = msg.device_id || consoleId;
      simulateAgentResponse(deviceId, msg, ws);

    } catch (e) {
      console.error('[Mock WS] Parse error:', e.message);
    }
  });

  ws.on('close', () => {
    console.log(`[Mock WS] Client disconnected: ${consoleId}`);
    clients.delete(consoleId);
  });

  ws.on('error', (err) => {
    console.error('[Mock WS] Error:', err.message);
  });
});

// API to check received messages and state
httpServer.getState = () => serverState;
httpServer.reset = () => {
  serverState.receivedMessages = [];
  serverState.confirmedDevices.clear();
  serverState.createdTasks = [];
  serverState.interruptedTasks = [];
};
httpServer.clients = clients;

httpServer.listen(PORT, mockServerConfig.host, () => {
  console.log(`[Mock Server] Running on http://${mockServerConfig.host}:${PORT}`);
  console.log(`[Mock Server] WebSocket on ws://${mockServerConfig.host}:${PORT}${WS_CONSOLE_PATH}`);
});

module.exports = httpServer;
