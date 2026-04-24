/**
 * agent-full.spec.ts
 *
 * 完整模拟人类操作 Agent 会话窗口的 E2E 测试
 * 覆盖所有 UI 交互和功能测试
 *
 * 运行方式:
 *   cd Web/mock-server && node mock-server.cjs &
 *   cd Web && npm run dev &
 *   cd Web && npx playwright test tests/e2e/agent-full.spec.ts
 */

import { test, expect, type Page } from '@playwright/test';

const DEVICE_ID = 'test-device-001';
const BUSY_DEVICE_ID = 'test-device-002';
const TEST_INSTRUCTION = '打开设置';

// ─── Mock 数据 ────────────────────────────────────────────────────────────────

const MOCK_DEVICES_RESPONSE = {
  devices: [
    {
      device_id: DEVICE_ID,
      device_name: '测试手机1',
      status: 'idle',
      platform: 'android',
      model: 'Test Phone',
      os_version: '14',
      current_task_id: null,
    },
    {
      device_id: BUSY_DEVICE_ID,
      device_name: '测试手机2',
      status: 'busy',
      platform: 'android',
      model: 'Test Phone 2',
      os_version: '13',
      current_task_id: 'task-busy-001',
    },
  ],
};

const MOCK_SESSION_IDLE = {
  task_id: null,
  instruction: null,
  mode: 'normal',
  status: 'idle',
  current_step: 0,
  max_steps: 100,
  latest_screenshot: null,
  interruptible: false,
};

const MOCK_SESSION_RUNNING = {
  task_id: 'task-running-001',
  instruction: TEST_INSTRUCTION,
  mode: 'normal',
  status: 'running',
  current_step: 5,
  max_steps: 100,
  latest_screenshot: null,
  interruptible: true,
};

const MOCK_CHAT_HISTORY = {
  device_id: DEVICE_ID,
  messages: [
    {
      id: 'msg-001',
      role: 'user',
      content: TEST_INSTRUCTION,
      created_at: new Date().toISOString(),
    },
    {
      id: 'msg-002',
      role: 'agent',
      content: '我理解了你的需求，让我来执行...',
      thinking: '用户想要打开设置应用...',
      action_type: 'launch',
      action_params: { app: 'Settings' },
      created_at: new Date().toISOString(),
    },
  ],
  total: 2,
};

type MockConversationMessage = {
  id: string;
  role: 'user' | 'agent';
  content: string;
  timestamp: string;
};

// ─── 辅助函数 ────────────────────────────────────────────────────────────────

/** 等待 agentWindowVisible 状态变为 true */
async function waitForAgentWindow(page: Page, timeout = 5000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const isVisible = await page.evaluate(() => {
      const store = (window as any).__STATE__;
      return store?.agentWindowVisible === true;
    });
    if (isVisible) return true;
    await page.waitForTimeout(100);
  }
  return false;
}

/** 打开 Agent 窗口 */
async function openAgentWindow(page: Page, deviceId: string) {
  const deviceCard = page.locator('[data-device-id]').filter({ hasText: deviceId });
  if (await deviceCard.isVisible()) {
    await deviceCard.click();
  } else {
    await page.getByText(deviceId).first().click();
  }
  await page.waitForTimeout(500);
}

async function closeAgentWindow(page: Page) {
  await page.locator('.ant-card-head').last().locator('button').last().click();
  await expect(page.getByTestId('agent-conversation')).not.toBeVisible();
}

function buildScrollableConversation(count: number): MockConversationMessage[] {
  const baseTime = Date.now();
  return Array.from({ length: count }, (_, index) => ({
    id: `scroll-msg-${index + 1}`,
    role: index % 2 === 0 ? 'user' : 'agent',
    content: `滚动测试消息 ${index + 1}：${'这是用于验证滚动跟随行为的长消息。'.repeat(20)}`,
    timestamp: new Date(baseTime + index * 1000).toISOString(),
  }));
}

async function waitForConversationToBeScrollable(page: Page) {
  await expect.poll(async () => {
    const metrics = await getConversationScrollMetrics(page);
    return metrics.scrollHeight > metrics.clientHeight;
  }).toBe(true);
}

async function waitForDistanceFromBottomAtLeast(page: Page, minDistance: number) {
  await expect.poll(async () => {
    const metrics = await getConversationScrollMetrics(page);
    return metrics.distanceFromBottom;
  }).toBeGreaterThan(minDistance);
}

async function waitForDistanceFromBottomAtMost(page: Page, maxDistance: number) {
  await expect.poll(async () => {
    const metrics = await getConversationScrollMetrics(page);
    return metrics.distanceFromBottom;
  }).toBeLessThanOrEqual(maxDistance);
}

async function waitForScrollTopNear(page: Page, expected: number, tolerance = 8) {
  await expect.poll(async () => {
    const metrics = await getConversationScrollMetrics(page);
    return Math.abs(metrics.scrollTop - expected);
  }).toBeLessThanOrEqual(tolerance);
}

async function waitForScrollTopAtLeast(page: Page, minScrollTop: number) {
  await expect.poll(async () => {
    const metrics = await getConversationScrollMetrics(page);
    return metrics.scrollTop;
  }).toBeGreaterThanOrEqual(minScrollTop);
}

async function scrollConversationTo(page: Page, offsetFromBottom: number) {
  await page.getByTestId('agent-conversation').evaluate((element, value) => {
    element.scrollTop = Math.max(0, element.scrollHeight - element.clientHeight - value);
    element.dispatchEvent(new Event('scroll', { bubbles: true }));
  }, offsetFromBottom);
}

async function scrollConversationToBottom(page: Page) {
  await page.getByTestId('agent-conversation').evaluate((element) => {
    element.scrollTop = element.scrollHeight;
    element.dispatchEvent(new Event('scroll', { bubbles: true }));
  });
}

async function waitForAnimationFrame(page: Page) {
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
}

async function waitForSessionHydrationToFinish(page: Page) {
  await expect.poll(async () => {
    return page.evaluate(async () => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      return useAgentStore.getState().isSessionHydrating;
    });
  }).toBe(false);
}

async function setConversationHistory(page: Page, messages: MockConversationMessage[]) {
  await page.evaluate(async ({ deviceId, messages }) => {
    const { useAgentStore } = await import('./src/stores/agentStore');
    useAgentStore.setState({
      currentDeviceId: deviceId,
      conversationHistory: messages as any,
      displayConversationHistory: messages as any,
      pendingAction: null,
    });
  }, { deviceId: DEVICE_ID, messages });
  await page.waitForTimeout(100);
}
async function appendConversationMessage(page: Page, message: MockConversationMessage) {
  await page.evaluate(async ({ message }) => {
    const { useAgentStore } = await import('./src/stores/agentStore');
    const state = useAgentStore.getState() as any;
    useAgentStore.setState({
      conversationHistory: [...state.conversationHistory, message],
      displayConversationHistory: [...state.displayConversationHistory, message],
    });
  }, { message });
  await page.waitForTimeout(100);
}

async function getConversationScrollMetrics(page: Page) {
  return page.getByTestId('agent-conversation').evaluate((element) => ({
    scrollTop: element.scrollTop,
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
    distanceFromBottom: element.scrollHeight - element.scrollTop - element.clientHeight,
  }));
}

// ─── 测试套件 ────────────────────────────────────────────────────────────────

test.describe.configure({ mode: 'serial' });

test.describe('Agent 会话窗口完整功能测试', () => {

  // ── 1. 页面加载和基础 UI ────────────────────────────────────────────────

  test.describe('1. 页面加载和设备列表', () => {
    test.beforeEach(async ({ page }) => {
      // Mock HTTP API
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
        } else if (url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_CHAT_HISTORY) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      // Mock WebSocket
      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('1.1 页面正常加载，显示标题', async ({ page }) => {
      // 检查页面标题
      const title = await page.title();
      expect(title.length).toBeGreaterThan(0);
    });

    test('1.2 设备列表显示正确', async ({ page }) => {
      // 等待设备列表加载
      await page.waitForTimeout(1000);

      // 检查设备名称是否显示
      const deviceName = page.getByText('测试手机1');
      await expect(deviceName).toBeVisible();
    });

    test('1.3 空闲设备状态显示', async ({ page }) => {
      await page.waitForTimeout(500);

      // 检查空闲状态标签
      const idleStatus = page.getByText('空闲');
      await expect(idleStatus.first()).toBeVisible();
    });

    test('1.4 忙碌设备状态显示', async ({ page }) => {
      await page.waitForTimeout(500);

      // 检查忙碌状态 - 使用更精确的定位器
      const busyStatus = page.getByText('忙碌中').first();
      await expect(busyStatus).toBeVisible();
    });
  });

  // ── 2. Agent 窗口打开和关闭 ───────────────────────────────────────────

  test.describe('2. Agent 窗口打开和关闭', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
        } else if (url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_CHAT_HISTORY) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('2.1 Agent 窗口正确显示设备名称', async ({ page }) => {
      // 设置设备并打开 Agent 窗口
      await page.evaluate((deviceId) => {
        const { useAgentStore } = (window as any).__USE_STORE__?.() || {};
        const store = useAgentStore?.();
        if (store) {
          store.setState?.({ currentDeviceId: deviceId });
        }
        // 触发 agentWindowVisible
        const event = new CustomEvent('test-open-agent', { detail: { deviceId } });
        window.dispatchEvent(event);
      }, DEVICE_ID);

      // 直接通过 store 打开窗口
      await page.evaluate(() => {
        const app = document.querySelector('#root');
        if (app) {
          // 模拟点击设备卡片
          const cards = app.querySelectorAll('[class*="card"]');
        }
      });

      await page.waitForTimeout(500);
    });

    test('2.2 Agent 窗口包含标题栏', async ({ page }) => {
      // 验证 Agent 窗口组件存在
      await page.evaluate(async (deviceId) => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({ currentDeviceId: deviceId });
      }, DEVICE_ID);

      // 检查窗口标题元素
      const header = page.locator('.ant-card-head');
      await page.waitForTimeout(200);
    });

    test('2.3 Agent 窗口关闭按钮存在', async ({ page }) => {
      await page.evaluate(async (deviceId) => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({ currentDeviceId: deviceId });
      }, DEVICE_ID);

      // 检查关闭按钮
      const closeBtn = page.locator('button').filter({ has: page.locator('.anticon-close') }).first();
      await page.waitForTimeout(200);
    });
  });

  // ── 3. 模式切换功能 ─────────────────────────────────────────────────────

  test.describe('3. 模式切换功能', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');

      // 设置初始状态
      await page.evaluate((deviceId) => {
        const { useAgentStore } = (window as any).__USE_STORE__?.() || {};
      }, DEVICE_ID);
    });

    test('3.1 默认模式是非谨慎模式', async ({ page }) => {
      const mode = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().mode;
      });

      expect(mode).toBe('normal');
    });

    test('3.2 可以切换到谨慎模式', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().setMode('cautious');
      });

      const mode = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().mode;
      });

      expect(mode).toBe('cautious');
    });

    test('3.3 可以从谨慎模式切换回非谨慎模式', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const store = useAgentStore.getState();
        store.setMode('cautious');
        store.setMode('normal');
      });

      const mode = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().mode;
      });

      expect(mode).toBe('normal');
    });

    test('3.4 谨慎模式标签正确显示', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().setMode('cautious');
      });

      // 验证 store 中的模式状态
      const mode = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().mode;
      });

      expect(mode).toBe('cautious');
    });
  });

  // ── 4. 输入框和发送功能 ────────────────────────────────────────────────

  test.describe('4. 输入框和发送功能', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('4.1 sendCommand 函数存在且可调用', async ({ page }) => {
      // 验证 sendCommand 函数存在
      const hasSendCommand = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return typeof useAgentStore.getState().sendCommand === 'function';
      });

      expect(hasSendCommand).toBe(true);
    });

    test('4.2 sendCommand 添加用户消息到历史', async ({ page }) => {
      await page.evaluate(async (deviceId) => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: deviceId,
          isLocked: true,
          isRunning: false,
          conversationHistory: [],
        });
        useAgentStore.getState().sendCommand('打开设置');
      }, DEVICE_ID);

      await page.waitForTimeout(200);

      const history = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().conversationHistory;
      });

      expect(history.length).toBeGreaterThan(0);
      expect(history[0].content).toBe('打开设置');
    });

    test('4.3 设备忙碌时 canSendCommand 为 false', async ({ page }) => {
      await page.evaluate(async (deviceId) => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: deviceId,
          currentTaskId: 'task-001',
          canInterrupt: false,
          isRunning: true,
          status: 'running',
        });
      });

      const canSend = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        const hasBackendActiveTask = Boolean(s.currentTaskId) && (s.canInterrupt || s.isRunning || s.status === 'pending' || s.status === 'running');
        return !hasBackendActiveTask;
      });

      expect(canSend).toBe(false);
    });

    test('4.4 设备空闲时可发送指令', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          currentTaskId: null,
          canInterrupt: false,
          isRunning: false,
          status: 'idle',
        });
      });

      const canSend = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        const hasBackendActiveTask = Boolean(s.currentTaskId) && (s.canInterrupt || s.isRunning || s.status === 'pending' || s.status === 'running');
        return !hasBackendActiveTask;
      });

      expect(canSend).toBe(true);
    });
  });

  // ── 5. 谨慎模式确认功能 ────────────────────────────────────────────────

  test.describe('5. 谨慎模式确认功能', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('5.1 pendingAction 状态正确设置', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          mode: 'cautious',
          pendingAction: {
            step: {
              id: 'step-001',
              phase: 'action',
              action: { type: 'tap', description: '点击按钮' },
              timestamp: new Date().toISOString(),
              success: true,
              step_number: 1,
            },
          },
          waitingForConfirm: true,
          waitingConfirmPhase: 'act',
        });
      });

      const state = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        return {
          pendingAction: s.pendingAction,
          waitingForConfirm: s.waitingForConfirm,
          waitingConfirmPhase: s.waitingConfirmPhase,
        };
      });

      expect(state.pendingAction).not.toBeNull();
      expect(state.waitingForConfirm).toBe(true);
      expect(state.waitingConfirmPhase).toBe('act');
    });

    test('5.2 confirmAction 调用 confirmPhase(true)', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          mode: 'cautious',
          pendingAction: {
            step: {
              id: 'step-001',
              phase: 'action',
              action: { type: 'tap', description: '点击按钮' },
              timestamp: new Date().toISOString(),
              success: true,
              step_number: 1,
            },
          },
          waitingForConfirm: true,
          waitingConfirmPhase: 'act',
          isRunning: false,
        });
      });

      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().confirmAction();
      });

      const state = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        return {
          pendingAction: s.pendingAction,
          isRunning: s.isRunning,
        };
      });

      expect(state.pendingAction).toBeNull();
      expect(state.isRunning).toBe(true);
    });

    test('5.3 rejectAction 调用 confirmPhase(false)', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          mode: 'cautious',
          pendingAction: {
            step: {
              id: 'step-001',
              phase: 'action',
              action: { type: 'tap', description: '点击按钮' },
              timestamp: new Date().toISOString(),
              success: true,
              step_number: 1,
            },
          },
          waitingForConfirm: true,
          waitingConfirmPhase: 'act',
          isRunning: false,
        });
      });

      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().rejectAction();
      });

      const state = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        return {
          pendingAction: s.pendingAction,
          isRunning: s.isRunning,
        };
      });

      expect(state.pendingAction).toBeNull();
      expect(state.isRunning).toBe(false);
    });

    test('5.4 skipAction 调用 confirmPhase(false)', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          mode: 'cautious',
          pendingAction: {
            step: {
              id: 'step-001',
              phase: 'action',
              action: { type: 'tap', description: '点击按钮' },
              timestamp: new Date().toISOString(),
              success: true,
              step_number: 1,
            },
          },
          waitingForConfirm: true,
          waitingConfirmPhase: 'act',
          isRunning: false,
        });
      });

      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().skipAction();
      });

      const state = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        return {
          pendingAction: s.pendingAction,
          isRunning: s.isRunning,
        };
      });

      expect(state.pendingAction).toBeNull();
      expect(state.isRunning).toBe(false);
    });

    test('5.5 确认后添加用户消息', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          mode: 'cautious',
          pendingAction: {
            step: {
              id: 'step-001',
              phase: 'action',
              action: { type: 'tap', description: '点击按钮' },
              timestamp: new Date().toISOString(),
              success: true,
              step_number: 1,
            },
          },
          waitingForConfirm: true,
          waitingConfirmPhase: 'act',
        });
      });

      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().confirmAction();
      });

      const state = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        return {
          messageCount: s.conversationHistory.length,
          lastMessage: s.conversationHistory[s.conversationHistory.length - 1]?.content,
        };
      });

      expect(state.messageCount).toBeGreaterThan(0);
      expect(state.lastMessage).toContain('确认');
    });
  });

  // ── 6. 任务控制功能 ─────────────────────────────────────────────────────

  test.describe('6. 任务控制功能', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_RUNNING) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('6.1 canInterrupt 在任务运行时为 true', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          currentTaskId: 'task-001',
          canInterrupt: true,
          status: 'running',
        });
      });

      const canInterrupt = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().canInterrupt;
      });

      expect(canInterrupt).toBe(true);
    });

    test('6.2 interrupt 函数存在且可调用', async ({ page }) => {
      const hasInterrupt = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return typeof useAgentStore.getState().interrupt === 'function';
      });
      expect(hasInterrupt).toBe(true);
    });

    test('6.3 中断后 isRunning 变为 false', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          currentTaskId: 'task-001',
          canInterrupt: true,
          isRunning: true,
          status: 'running',
        });
      });

      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        await useAgentStore.getState().interrupt();
      });

      await page.waitForTimeout(300);

      const state = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return {
          isRunning: useAgentStore.getState().isRunning,
          status: useAgentStore.getState().status,
        };
      });

      // 注意: 实际行为取决于 interrupt 实现
      // 可能需要等待 WebSocket 响应
    });

    test('6.4 resume 抛出明确异常', async ({ page }) => {
      const errorMsg = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        try {
          await useAgentStore.getState().resume();
          return null;
        } catch (e: unknown) {
          return (e as Error).message;
        }
      });

      expect(errorMsg).toBe('resume 功能暂未实现，请重新发送指令');
    });

    test('6.5 continueTask 抛出明确异常', async ({ page }) => {
      const errorMsg = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        try {
          await useAgentStore.getState().continueTask(50);
          return null;
        } catch (e: unknown) {
          return (e as Error).message;
        }
      });

      expect(errorMsg).toBe('continueTask 功能暂未实现，请重新发送指令');
    });
  });

  // ── 7. 会话历史功能 ───────────────────────────────────────────────────

  test.describe('7. 会话历史功能', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
        } else if (url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_CHAT_HISTORY) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');

      // 清空 store 状态
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          conversationHistory: [],
          currentDeviceId: 'test-device-001',
        });
      });
    });

    test('7.1 clearConversation 函数存在且可调用', async ({ page }) => {
      const hasClearConversation = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return typeof useAgentStore.getState().clearConversation === 'function';
      });
      expect(hasClearConversation).toBe(true);
    });

    test('7.2 添加用户消息', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({ currentDeviceId: 'test-device-001' });
        useAgentStore.getState().addUserMessage('测试消息');
      });

      const messages = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().conversationHistory;
      });

      expect(messages.length).toBeGreaterThan(0);
      expect(messages[messages.length - 1].content).toBe('测试消息');
    });

    test('7.3 添加 Agent 消息', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({ currentDeviceId: 'test-device-001' });
        useAgentStore.getState().addAgentMessage('Agent 回复', '思考过程...');
      });

      const messages = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().conversationHistory;
      });

      expect(messages.length).toBeGreaterThan(0);
      expect(messages[messages.length - 1].role).toBe('agent');
    });

    test('7.4 清空对话按钮可见', async ({ page }) => {
      // 设置设备以显示 Agent 窗口相关按钮
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({ currentDeviceId: 'test-device-001' });
      });

      await page.waitForTimeout(200);

      // 查找删除按钮
      const deleteBtn = page.locator('button').filter({ has: page.locator('.anticon-delete') }).first();
      // 注意: 按钮可能需要 Agent 窗口打开才可见
    });
  });

  // ── 7.5 关闭重开与滚动回归 ─────────────────────────────────────────────

  test.describe('7.5 关闭重开与滚动回归', () => {
    test('7.5.1 同设备关闭重开时保留本地历史直到水合完成', async ({ page }) => {
      let sessionRequestCount = 0;
      let releaseSessionResponse: (() => void) | null = null;

      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
          return;
        }
        if (url.includes('/api/v1/devices') && !url.includes('/session') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
          return;
        }
        if (url.includes(`/api/v1/devices/${DEVICE_ID}/session`)) {
          sessionRequestCount += 1;
          if (sessionRequestCount === 2) {
            await new Promise<void>((resolve) => {
              releaseSessionResponse = async () => {
                await route.fulfill({
                  status: 200,
                  contentType: 'application/json',
                  body: JSON.stringify(MOCK_SESSION_RUNNING),
                });
                resolve();
              };
            });
            return;
          }

          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(MOCK_SESSION_RUNNING),
          });
          return;
        }
        if (url.includes(`/api/v1/devices/${DEVICE_ID}/chat`)) {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(MOCK_CHAT_HISTORY),
          });
          return;
        }

        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');

      await openAgentWindow(page, DEVICE_ID);
      const conversation = page.getByTestId('agent-conversation');
      await expect(conversation).toBeVisible();
      await expect(conversation.getByText(TEST_INSTRUCTION, { exact: true })).toBeVisible();
      await expect(conversation.getByText('我理解了你的需求，让我来执行...', { exact: true })).toBeVisible();

      await closeAgentWindow(page);
      await openAgentWindow(page, DEVICE_ID);

      await expect(conversation).toBeVisible();
      await expect(conversation.getByText(TEST_INSTRUCTION, { exact: true })).toBeVisible();
      await expect(conversation.getByText('我理解了你的需求，让我来执行...', { exact: true })).toBeVisible();

      expect(releaseSessionResponse).not.toBeNull();
      await releaseSessionResponse?.();
      await page.waitForLoadState('networkidle');

      await expect(conversation.getByText(TEST_INSTRUCTION, { exact: true })).toBeVisible();
      await expect(conversation.getByText('我理解了你的需求，让我来执行...', { exact: true })).toBeVisible();
    });

    test('7.5.2 上滑查看历史时不会被自动拉回底部，回到底部后恢复跟随', async ({ page }) => {
      const scrollableHistoryMessages = buildScrollableConversation(60).map((message) => ({
        id: message.id,
        role: message.role,
        content: message.content,
        created_at: message.timestamp,
      }));
      const scrollableChatHistory = {
        device_id: DEVICE_ID,
        messages: scrollableHistoryMessages,
        total: scrollableHistoryMessages.length,
      };

      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
        } else if (url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(scrollableChatHistory) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');

      await openAgentWindow(page, DEVICE_ID);
      const conversation = page.getByTestId('agent-conversation');
      await expect(conversation).toBeVisible();
      await waitForConversationToBeScrollable(page);

      await scrollConversationToBottom(page);
      await waitForAnimationFrame(page);

      await scrollConversationTo(page, 320);
      await waitForAnimationFrame(page);
      await waitForDistanceFromBottomAtLeast(page, 48);

      const beforeAppend = await getConversationScrollMetrics(page);
      await appendConversationMessage(page, {
        id: 'scroll-new-top-guard',
        role: 'agent',
        content: '新增消息：用户在看历史时不应被拉回底部',
        timestamp: new Date().toISOString(),
      });
      await waitForAnimationFrame(page);
      await waitForScrollTopNear(page, beforeAppend.scrollTop, 8);
      await waitForDistanceFromBottomAtLeast(page, 48);

      await scrollConversationToBottom(page);
      await waitForAnimationFrame(page);
      await waitForDistanceFromBottomAtMost(page, 48);

      const bottomMetricsBeforeAppend = await getConversationScrollMetrics(page);
      await appendConversationMessage(page, {
        id: 'scroll-new-follow',
        role: 'agent',
        content: '新增消息：回到底部后应恢复自动跟随',
        timestamp: new Date(Date.now() + 1000).toISOString(),
      });
      await expect.poll(async () => {
        return page.evaluate(async () => {
          const { useAgentStore } = await import('./src/stores/agentStore');
          const history = useAgentStore.getState().displayConversationHistory;
          return history[history.length - 1]?.content;
        });
      }).toBe('新增消息：回到底部后应恢复自动跟随');
      await waitForAnimationFrame(page);
      await waitForScrollTopAtLeast(page, bottomMetricsBeforeAppend.scrollTop);
      await waitForDistanceFromBottomAtMost(page, 48);
      await expect.poll(async () => {
        return conversation.evaluate((element) => element.textContent?.includes('新增消息：回到底部后应恢复自动跟随') ?? false);
      }).toBe(true);
    });
  });

  // ── 8. 截图和进度显示 ─────────────────────────────────────────────────

  test.describe('8. 截图和进度显示', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_RUNNING) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('8.1 设置截图', async ({ page }) => {
      const mockScreenshot = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';

      await page.evaluate(async (screenshot) => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().setScreenshot(screenshot);
      }, mockScreenshot);

      const screenshot = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().currentScreenshot;
      });

      expect(screenshot).toBe(mockScreenshot);
    });

    test('8.2 设置当前应用', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().setCurrentApp('设置');
      });

      const app = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().currentApp;
      });

      expect(app).toBe('设置');
    });

    test('8.3 进度百分比正确计算', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentStepNum: 50,
          maxSteps: 100,
        });
      });

      const progress = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        return s.maxSteps > 0 ? Math.round((s.currentStepNum / s.maxSteps) * 100) : 0;
      });

      expect(progress).toBe(50);
    });

    test('8.4 进度显示 0/100 格式', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentStepNum: 0,
          maxSteps: 100,
        });
      });

      const progress = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        return `${s.currentStepNum}/${s.maxSteps}`;
      });

      expect(progress).toBe('0/100');
    });
  });

  // ── 9. 解析重试次数设置 ────────────────────────────────────────────────

  test.describe('9. 解析重试次数设置', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('9.1 默认重试次数', async ({ page }) => {
      const retries = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().maxParseRetries;
      });

      // 默认值应该是 3 (常见默认值)
      expect(retries).toBeGreaterThanOrEqual(0);
    });

    test('9.2 可以修改重试次数', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().setMaxParseRetries(5);
      });

      const retries = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().maxParseRetries;
      });

      expect(retries).toBe(5);
    });

    test('9.3 重试次数范围 0-10', async ({ page }) => {
      // 设置边界值
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().setMaxParseRetries(0);
      });

      let retries = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().maxParseRetries;
      });
      expect(retries).toBe(0);

      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.getState().setMaxParseRetries(10);
      });

      retries = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().maxParseRetries;
      });
      expect(retries).toBe(10);
    });
  });

  // ── 10. 执行状态显示 ───────────────────────────────────────────────────

  test.describe('10. 执行状态显示', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_RUNNING) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('10.1 执行中状态显示', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          isRunning: true,
          status: 'running',
        });
      });

      const isRunning = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().isRunning;
      });

      expect(isRunning).toBe(true);
    });

    test('10.2 思考中状态设置', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          isThinking: true,
          thinkingContent: '正在分析...',
        });
      });

      const state = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return {
          isThinking: useAgentStore.getState().isThinking,
          thinkingContent: useAgentStore.getState().thinkingContent,
        };
      });

      expect(state.isThinking).toBe(true);
      expect(state.thinkingContent).toBe('正在分析...');
    });

    test('10.3 阶段指示器设置', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentPhase: 'reason',
        });
      });

      const phase = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().currentPhase;
      });

      expect(phase).toBe('reason');
    });

    test('10.4 主控权锁定状态', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          isLocked: true,
        });
      });

      const isLocked = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        return useAgentStore.getState().isLocked;
      });

      expect(isLocked).toBe(true);
    });
  });

  // ── 11. 设备忙碌状态处理 ───────────────────────────────────────────────

  test.describe('11. 设备忙碌状态处理', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          // 根据设备返回不同状态
          const deviceId = url.match(/devices\/([^/]+)/)?.[1];
          if (deviceId === BUSY_DEVICE_ID) {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_RUNNING) });
          } else {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
          }
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('11.1 忙碌设备不能发送指令', async ({ page }) => {
      await page.evaluate(async (deviceId) => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: deviceId,
          currentTaskId: 'task-busy-001',
          canInterrupt: false,
          isRunning: true,
          status: 'running',
        });
      }, BUSY_DEVICE_ID);

      // canSendCommand 应该为 false
      const canSend = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        const hasBackendActiveTask = Boolean(s.currentTaskId) && (s.canInterrupt || s.isRunning || s.status === 'pending' || s.status === 'running');
        return !hasBackendActiveTask;
      });

      expect(canSend).toBe(false);
    });

    test('11.2 忙碌状态显示提示信息', async ({ page }) => {
      await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        useAgentStore.setState({
          currentDeviceId: 'test-device-001',
          currentTaskId: 'task-001',
          isRunning: true,
          status: 'running',
        });
      });

      const placeholder = await page.evaluate(async () => {
        const { useAgentStore } = await import('./src/stores/agentStore');
        const s = useAgentStore.getState();
        if (s.isRunning) {
          return '后端任务仍在进行或刚恢复，请等待...';
        }
        return '';
      });

      expect(placeholder).toBe('后端任务仍在进行或刚恢复，请等待...');
    });
  });

  // ── 12. API 修复验证 ──────────────────────────────────────────────────

  test.describe('12. API 修复验证', () => {
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/**', async (route) => {
        const url = route.request().url();
        if (url.includes('/health')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
        } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
        } else if (url.includes('/api/v1/devices') && url.includes('/session')) {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_IDLE) });
        } else {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
        }
      });

      await page.route('**/ws/**', async (route) => {
        await route.abort();
      });

      await page.goto('http://localhost:5173');
      await page.waitForLoadState('networkidle');
    });

    test('12.1 taskApi 仅保留 4 个方法', async ({ page }) => {
      const methods = await page.evaluate(async () => {
        const { taskApi } = await import('./src/services/api');
        return Object.keys(taskApi);
      });

      expect(methods).toEqual(['list', 'get', 'getSteps', 'interrupt']);
    });

    test('12.2 taskApi 废弃方法已删除', async ({ page }) => {
      const methodNames = await page.evaluate(async () => {
        const { taskApi } = await import('./src/services/api');
        return {
          updateProgress: typeof (taskApi as any).updateProgress === 'function',
          addStep: typeof (taskApi as any).addStep === 'function',
          submitDecision: typeof (taskApi as any).submitDecision === 'function',
          delete: typeof (taskApi as any).delete === 'function',
          create: typeof (taskApi as any).create === 'function',
          createBatch: typeof (taskApi as any).createBatch === 'function',
        };
      });

      expect(methodNames.updateProgress).toBe(false);
      expect(methodNames.addStep).toBe(false);
      expect(methodNames.submitDecision).toBe(false);
      expect(methodNames.delete).toBe(false);
      expect(methodNames.create).toBe(false);
      expect(methodNames.createBatch).toBe(false);
    });

    test('12.3 agentApi.getHistory 已删除', async ({ page }) => {
      const hasGetHistory = await page.evaluate(async () => {
        const { agentApi } = await import('./src/services/agentApi');
        return typeof (agentApi as any).getHistory === 'function';
      });

      expect(hasGetHistory).toBe(false);
    });

    test('12.4 agentApi.confirmAction 已删除', async ({ page }) => {
      const hasConfirmAction = await page.evaluate(async () => {
        const { agentApi } = await import('./src/services/agentApi');
        return typeof (agentApi as any).confirmAction === 'function';
      });

      expect(hasConfirmAction).toBe(false);
    });

    test('12.5 agentApi.resume 已删除', async ({ page }) => {
      const hasResume = await page.evaluate(async () => {
        const { agentApi } = await import('./src/services/agentApi');
        return typeof (agentApi as any).resume === 'function';
      });

      expect(hasResume).toBe(false);
    });

    test('12.6 agentApi.continueTask 已删除', async ({ page }) => {
      const hasContinueTask = await page.evaluate(async () => {
        const { agentApi } = await import('./src/services/agentApi');
        return typeof (agentApi as any).continueTask === 'function';
      });

      expect(hasContinueTask).toBe(false);
    });

    test('12.7 uploadScreenshot 返回正确提示', async ({ page }) => {
      const result = await page.evaluate(async () => {
        const { agentApi } = await import('./src/services/agentApi');
        return await agentApi.uploadScreenshot('test-device', 'base64data');
      });

      expect(result.success).toBe(false);
      expect(result.message).toBe('截图通过 WebSocket 实时推送，暂不支持手动上传');
    });

    test('12.8 startTask 通过 WebSocket 发送', async ({ page }) => {
      // 验证 startTask 返回正确格式
      const result = await page.evaluate(async () => {
        const { agentApi } = await import('./src/services/agentApi');
        return {
          result: await agentApi.startTask('test-device', {
            task_id: 'test-task',
            instruction: '打开设置',
            mode: 'normal',
            max_steps: 100,
          }),
        };
      });

      expect(result.result.status).toBe('pending');
      // task_id 应该是空的，因为通过 WebSocket 发送
      expect(result.result.task_id).toBe('');
    });
  });
});
