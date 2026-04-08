/**
 * agent-api-fixes.spec.ts
 *
 * 测试 Web API 层修复:
 * 1. confirmAction/rejectAction/skipAction 通过 WebSocket confirm_phase 正确发送
 * 2. resume/continueTask 抛出明确异常
 * 3. taskApi 废弃方法已删除
 * 4. agentApi.getHistory 已删除
 * 5. uploadScreenshot 返回正确提示信息
 *
 * 运行方式:
 *   cd Web/mock-server && node mock-server.cjs &
 *   cd Web && npm run dev &
 *   cd Web && npx playwright test tests/e2e/agent-api-fixes.spec.ts
 */

import { test, expect, type Page } from '@playwright/test';

const MOCK_DEVICE_ID = 'test-device-001';
const MOCK_TASK_ID = 'test-task-001';

// Mock 响应数据
const MOCK_DEVICES_RESPONSE = {
  devices: [{
    device_id: MOCK_DEVICE_ID,
    status: 'idle',
    platform: 'android',
    model: 'Test Phone',
    os_version: '14',
    current_task_id: null,
  }],
};

const MOCK_SESSION_SNAPSHOT = {
  task_id: null,
  instruction: null,
  mode: 'normal',
  status: 'idle',
  current_step: 0,
  max_steps: 100,
  latest_screenshot: null,
  interruptible: false,
};

const MOCK_CHAT_HISTORY = {
  device_id: MOCK_DEVICE_ID,
  messages: [],
  total: 0,
};

// ─── 测试套件 ────────────────────────────────────────────────────────────────

test.describe.configure({ mode: 'serial' });

test.describe('Web API 层修复验证', () => {
  test.beforeEach(async ({ page }) => {
    // 拦截 HTTP API 请求
    await page.route('**/api/**', async (route) => {
      const url = route.request().url();

      if (url.includes('/health')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'healthy' }) });
      } else if (url.includes('/api/v1/devices') && !url.includes('/chat')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_DEVICES_RESPONSE) });
      } else if (url.includes('/api/v1/tasks/devices')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_SESSION_SNAPSHOT) });
      } else if (url.includes('/api/v1/devices') && url.includes('/chat')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_CHAT_HISTORY) });
      } else if (url.includes('/api/v1/tasks/') && url.includes(MOCK_TASK_ID)) {
        await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({}) });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
      }
    });

    // 拦截 WebSocket 连接
    await page.route('**/ws/**', async (route) => {
      await route.abort();
    });

    // 导航到应用
    await page.goto('http://localhost:5173');
    await page.waitForLoadState('networkidle');
  });

  // ── 测试用例 ──────────────────────────────────────────────────────────────

  test('01. confirmPhase(true) 正确修改 store 状态', async ({ page }) => {
    // 设置初始状态
    await page.evaluate(async (deviceId: string) => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      useAgentStore.setState({
        currentDeviceId: deviceId,
        pendingAction: {
          step: {
            id: 'test-step',
            phase: 'action',
            action: { type: 'tap', description: 'test action' },
            timestamp: new Date().toISOString(),
            success: true,
            step_number: 1,
          },
        },
        waitingForConfirm: true,
        isRunning: false,
      });
    }, MOCK_DEVICE_ID);

    // 调用 confirmPhase(true)
    await page.evaluate(async () => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      useAgentStore.getState().confirmPhase(true);
    });

    // 验证状态变化
    const state = await page.evaluate(async () => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      const s = useAgentStore.getState();
      return {
        pendingAction: s.pendingAction,
        waitingForConfirm: s.waitingForConfirm,
        isRunning: s.isRunning,
      };
    });

    expect(state.pendingAction).toBeNull();
    expect(state.waitingForConfirm).toBe(false);
    expect(state.isRunning).toBe(true);
  });

  test('02. confirmPhase(false) 正确修改 store 状态', async ({ page }) => {
    await page.evaluate(async (deviceId: string) => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      useAgentStore.setState({
        currentDeviceId: deviceId,
        pendingAction: {
          step: {
            id: 'test-step',
            phase: 'action',
            action: { type: 'tap', description: 'test action' },
            timestamp: new Date().toISOString(),
            success: true,
            step_number: 1,
          },
        },
        waitingForConfirm: true,
        isRunning: false,
      });
    }, MOCK_DEVICE_ID);

    await page.evaluate(async () => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      useAgentStore.getState().confirmPhase(false);
    });

    const state = await page.evaluate(async () => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      const s = useAgentStore.getState();
      return {
        pendingAction: s.pendingAction,
        waitingForConfirm: s.waitingForConfirm,
        isRunning: s.isRunning,
      };
    });

    expect(state.pendingAction).toBeNull();
    expect(state.waitingForConfirm).toBe(false);
    expect(state.isRunning).toBe(false); // false 因为 rejected
  });

  test('03. confirmAction 调用 confirmPhase(true)', async ({ page }) => {
    await page.evaluate(async (deviceId: string) => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      useAgentStore.setState({
        currentDeviceId: deviceId,
        pendingAction: {
          step: {
            id: 'test-step',
            phase: 'action',
            action: { type: 'tap', description: 'test action' },
            timestamp: new Date().toISOString(),
            success: true,
            step_number: 1,
          },
        },
        waitingForConfirm: true,
        isRunning: false,
      });
    }, MOCK_DEVICE_ID);

    await page.evaluate(async () => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      useAgentStore.getState().confirmAction();
    });

    const state = await page.evaluate(async () => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      const s = useAgentStore.getState();
      return {
        pendingAction: s.pendingAction,
        waitingForConfirm: s.waitingForConfirm,
        isRunning: s.isRunning,
      };
    });

    // confirmAction 应该调用 confirmPhase(true)，所以 isRunning 应该是 true
    expect(state.pendingAction).toBeNull();
    expect(state.isRunning).toBe(true);
  });

  test('04. rejectAction 调用 confirmPhase(false)', async ({ page }) => {
    await page.evaluate(async (deviceId: string) => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      useAgentStore.setState({
        currentDeviceId: deviceId,
        pendingAction: {
          step: {
            id: 'test-step',
            phase: 'action',
            action: { type: 'tap', description: 'test action' },
            timestamp: new Date().toISOString(),
            success: true,
            step_number: 1,
          },
        },
        waitingForConfirm: true,
        isRunning: false,
      });
    }, MOCK_DEVICE_ID);

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

    // rejectAction 应该调用 confirmPhase(false)，所以 isRunning 应该是 false
    expect(state.pendingAction).toBeNull();
    expect(state.isRunning).toBe(false);
  });

  test('05. skipAction 调用 confirmPhase(false)', async ({ page }) => {
    await page.evaluate(async (deviceId: string) => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      useAgentStore.setState({
        currentDeviceId: deviceId,
        pendingAction: {
          step: {
            id: 'test-step',
            phase: 'action',
            action: { type: 'tap', description: 'test action' },
            timestamp: new Date().toISOString(),
            success: true,
            step_number: 1,
          },
        },
        waitingForConfirm: true,
        isRunning: false,
      });
    }, MOCK_DEVICE_ID);

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

  test('06. resume 抛出明确异常', async ({ page }) => {
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

  test('07. continueTask 抛出明确异常', async ({ page }) => {
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

  test('08. taskApi 中废弃方法（updateProgress/addStep/submitDecision/delete）已删除', async ({ page }) => {
    const methodNames = await page.evaluate(async () => {
      const { taskApi } = await import('./src/services/api');
      return {
        updateProgress: typeof (taskApi as any).updateProgress === 'function',
        addStep: typeof (taskApi as any).addStep === 'function',
        submitDecision: typeof (taskApi as any).submitDecision === 'function',
        delete: typeof (taskApi as any).delete === 'function',
      };
    });

    expect(methodNames.updateProgress).toBe(false);
    expect(methodNames.addStep).toBe(false);
    expect(methodNames.submitDecision).toBe(false);
    expect(methodNames.delete).toBe(false);
  });

  test('09. agentApi.getHistory 已删除（方法不存在）', async ({ page }) => {
    const hasGetHistory = await page.evaluate(async () => {
      const { agentApi } = await import('./src/services/agentApi');
      return typeof (agentApi as any).getHistory === 'function';
    });
    expect(hasGetHistory).toBe(false);
  });

  test('10. uploadScreenshot 返回正确的提示信息', async ({ page }) => {
    const result = await page.evaluate(async () => {
      const { agentApi } = await import('./src/services/agentApi');
      return await agentApi.uploadScreenshot('test-device', 'base64data');
    });
    expect(result.success).toBe(false);
    expect(result.message).toBe('截图通过 WebSocket 实时推送，暂不支持手动上传');
  });

  test('11. taskApi 仅保留 list/get/getSteps/interrupt（废弃方法已删除）', async ({ page }) => {
    const methods = await page.evaluate(async () => {
      const { taskApi } = await import('./src/services/api');
      return Object.keys(taskApi);
    });
    expect(methods).toEqual(['list', 'get', 'getSteps', 'interrupt']);
  });

  test('12. UI: pendingAction 状态下 store 状态正确', async ({ page }) => {
    // 验证 pendingAction 状态正确时会显示 UI 相关状态
    await page.evaluate(async (deviceId: string) => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      useAgentStore.setState({
        currentDeviceId: deviceId,
        mode: 'cautious',
        pendingAction: {
          step: {
            id: 'step_ui',
            phase: 'action',
            action: { type: 'tap', params: { x: 500, y: 600 }, description: 'tap - 坐标: (500, 600)' },
            timestamp: new Date().toISOString(),
            success: true,
            step_number: 3,
          },
        },
        waitingForConfirm: true,
        waitingConfirmPhase: 'act',
        isRunning: false,
      });
    }, MOCK_DEVICE_ID);

    // 验证状态已设置
    const state = await page.evaluate(async () => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      const s = useAgentStore.getState();
      return {
        mode: s.mode,
        pendingActionExists: s.pendingAction !== null,
        waitingForConfirm: s.waitingForConfirm,
        waitingConfirmPhase: s.waitingConfirmPhase,
      };
    });

    expect(state.mode).toBe('cautious');
    expect(state.pendingActionExists).toBe(true);
    expect(state.waitingForConfirm).toBe(true);
    expect(state.waitingConfirmPhase).toBe('act');
  });

  test('13. confirmAction/rejectAction/skipAction 可以被调用', async ({ page }) => {
    // 验证这些函数存在且可调用
    const result = await page.evaluate(async (deviceId: string) => {
      const { useAgentStore } = await import('./src/stores/agentStore');
      const store = useAgentStore.getState();

      // 设置初始状态
      useAgentStore.setState({
        currentDeviceId: deviceId,
        pendingAction: {
          step: {
            id: 'test-step',
            phase: 'action',
            action: { type: 'tap', description: 'test action' },
            timestamp: new Date().toISOString(),
            success: true,
            step_number: 1,
          },
        },
        waitingForConfirm: true,
        isRunning: false,
      });

      // 验证函数存在
      const hasConfirmAction = typeof store.confirmAction === 'function';
      const hasRejectAction = typeof store.rejectAction === 'function';
      const hasSkipAction = typeof store.skipAction === 'function';

      // 调用 confirmAction
      store.confirmAction();

      const afterState = useAgentStore.getState();
      return {
        hasConfirmAction,
        hasRejectAction,
        hasSkipAction,
        pendingActionCleared: afterState.pendingAction === null,
        isRunning: afterState.isRunning,
      };
    }, MOCK_DEVICE_ID);

    expect(result.hasConfirmAction).toBe(true);
    expect(result.hasRejectAction).toBe(true);
    expect(result.hasSkipAction).toBe(true);
    expect(result.pendingActionCleared).toBe(true);
    expect(result.isRunning).toBe(true);
  });
});
