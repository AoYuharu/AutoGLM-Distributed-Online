import { test, expect } from '@playwright/test';

const DEVICE_A = 'batch-device-001';
const DEVICE_B = 'batch-device-002';

const mockDevicesResponse = {
  devices: [
    {
      id: '1',
      device_id: DEVICE_A,
      client_id: 'client-a',
      platform: 'android',
      device_name: 'Pixel Batch A',
      os_version: '14',
      screen_width: 1080,
      screen_height: 2400,
      status: 'idle',
      connection: 'usb',
      last_seen: '2026-04-11T10:00:00.000Z',
      current_task_id: null,
      current_app: null,
      remark: 'batch test device A',
    },
    {
      id: '2',
      device_id: DEVICE_B,
      client_id: 'client-b',
      platform: 'android',
      device_name: 'Pixel Batch B',
      os_version: '14',
      screen_width: 1080,
      screen_height: 2400,
      status: 'error',
      connection: 'usb',
      last_seen: '2026-04-11T10:00:01.000Z',
      current_task_id: null,
      current_app: null,
      remark: 'batch test device B',
    },
    {
      id: '3',
      device_id: 'batch-device-003',
      client_id: 'client-c',
      platform: 'android',
      device_name: 'Busy Device',
      os_version: '14',
      screen_width: 1080,
      screen_height: 2400,
      status: 'busy',
      connection: 'usb',
      last_seen: '2026-04-11T10:00:02.000Z',
      current_task_id: 'busy-task',
      current_app: 'com.example.busy',
      remark: 'should not be selectable',
    },
  ],
};

test.describe('Batch task page', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      class MockWebSocket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        static CLOSED = 3;
        static instances: MockWebSocket[] = [];

        url: string;
        readyState = MockWebSocket.CONNECTING;
        onopen: ((event: Event) => void) | null = null;
        onmessage: ((event: MessageEvent) => void) | null = null;
        onclose: ((event: CloseEvent) => void) | null = null;
        onerror: ((event: Event) => void) | null = null;
        sentMessages: any[] = [];

        constructor(url: string) {
          this.url = url;
          MockWebSocket.instances.push(this);
          setTimeout(() => {
            this.readyState = MockWebSocket.OPEN;
            this.onopen?.(new Event('open'));
          }, 0);
        }

        send(data: string) {
          this.sentMessages.push(JSON.parse(data));
        }

        close() {
          this.readyState = MockWebSocket.CLOSED;
          this.onclose?.(new CloseEvent('close', { code: 1000, reason: 'mock close' }));
        }
      }

      Object.defineProperty(window, 'WebSocket', {
        configurable: true,
        writable: true,
        value: MockWebSocket,
      });

      Object.defineProperty(window, '__getMockWsMessages', {
        configurable: true,
        value: () => {
          const instances = MockWebSocket.instances;
          return instances.flatMap((instance) => instance.sentMessages);
        },
      });

      Object.defineProperty(window, '__emitMockWsMessage', {
        configurable: true,
        value: (message: unknown) => {
          const instances = MockWebSocket.instances;
          const ws = instances[instances.length - 1];
          if (!ws?.onmessage) {
            throw new Error('No active mock websocket listener');
          }
          ws.onmessage(new MessageEvent('message', { data: JSON.stringify(message) }));
        },
      });

      Object.defineProperty(window, '__getLastMockWsReadyState', {
        configurable: true,
        value: () => {
          const instances = MockWebSocket.instances;
          const ws = instances[instances.length - 1];
          return ws?.readyState ?? null;
        },
      });
    });

    await page.route('**/health', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'healthy' }),
      });
    });

    await page.route('**/api/**', async (route) => {
      const url = route.request().url();
      const method = route.request().method();

      if (url.endsWith('/api/v1/devices') && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(mockDevicesResponse),
        });
        return;
      }

      if (url.includes('/api/v1/devices/') && url.includes('/session') && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            task_id: null,
            instruction: null,
            mode: 'normal',
            status: 'idle',
            current_step: 0,
            max_steps: 100,
            latest_screenshot: null,
            interruptible: false,
          }),
        });
        return;
      }

      if (url.includes('/api/v1/devices/') && url.includes('/interrupt') && method === 'POST') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ success: true }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.goto('http://localhost:5173');
    await page.waitForLoadState('networkidle');
  });

  test('shows start button before executions and advances on task_created/agent_status', async ({ page }) => {
    await page.getByText('批处理').click();

    await expect(page.getByText('共 2 台可用设备，已选择 0 台')).toBeVisible();
    await expect(page.getByText('Busy Device')).not.toBeVisible();

    await page.getByRole('button', { name: /全\s*选/ }).click();
    await expect(page.getByText('共 2 台可用设备，已选择 2 台')).toBeVisible();

    await page.getByRole('button', { name: /下一步/ }).click();
    await expect(page.getByRole('textbox', { name: /任务指令/i })).toBeVisible();

    await page.getByPlaceholder('输入自然语言任务指令，例如：打开微信搜索附近的人').fill('打开设置并检查网络');
    await page.getByRole('button', { name: /开始执行/ }).click();

    await expect(page.getByText('执行进度', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '开始执行' })).toBeVisible();
    await expect(page.getByText('当前还没有执行项。点击“开始执行”后，会先按设备提交 create_task')).toBeVisible();

    await expect.poll(async () => {
      return page.evaluate(() => (window as any).__getLastMockWsReadyState());
    }).toBe(1);

    await page.getByRole('button', { name: '开始执行' }).click();

    await expect(page.getByText('Pixel Batch A')).toBeVisible();
    await expect(page.getByText('Pixel Batch B')).toBeVisible();
    await expect(page.getByText('等待创建任务')).toHaveCount(2);
    await expect(page.getByText('等待 task_created')).toHaveCount(2);

    await expect.poll(async () => {
      return page.evaluate(() => (window as any).__getMockWsMessages());
    }).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: 'subscribe', device_id: DEVICE_A }),
        expect.objectContaining({ type: 'subscribe', device_id: DEVICE_B }),
        expect.objectContaining({ type: 'create_task', device_id: DEVICE_A, instruction: '打开设置并检查网络' }),
        expect.objectContaining({ type: 'create_task', device_id: DEVICE_B, instruction: '打开设置并检查网络' }),
      ])
    );

    await page.evaluate((deviceA) => {
      (window as any).__emitMockWsMessage({
        type: 'task_created',
        device_id: deviceA,
        task_id: 'task-batch-a',
        status: 'pending',
      });
      (window as any).__emitMockWsMessage({
        type: 'agent_status',
        device_id: deviceA,
        task_id: 'task-batch-a',
        status: 'running',
        step_number: 3,
        max_steps: 100,
        message: 'A running',
      });
    }, DEVICE_A);

    await expect(page.getByText('taskId: task-batch-a')).toBeVisible();
    await expect(page.getByText('执行中')).toBeVisible();
    await expect(page.getByText('A running')).toBeVisible();

    await page.evaluate(({ deviceA, deviceB }) => {
      (window as any).__emitMockWsMessage({
        type: 'task_created',
        device_id: deviceB,
        task_id: 'task-batch-b',
        status: 'pending',
      });
      (window as any).__emitMockWsMessage({
        type: 'agent_status',
        device_id: deviceB,
        task_id: 'task-batch-b',
        status: 'completed',
        step_number: 100,
        max_steps: 100,
        message: 'B done',
      });
      (window as any).__emitMockWsMessage({
        type: 'agent_status',
        device_id: deviceA,
        task_id: 'task-batch-a',
        status: 'finished',
        step_number: 100,
        max_steps: 100,
        message: 'A done',
      });
    }, { deviceA: DEVICE_A, deviceB: DEVICE_B });

    await expect(page.getByText('taskId: task-batch-b')).toBeVisible();
    await expect(page.getByText('已完成')).toHaveCount(2);
    await expect(page.getByText('2 / 2 完成')).toBeVisible();
    await expect(page.getByText('A done')).toBeVisible();
    await expect(page.getByText('B done')).toBeVisible();
  });

  test('interrupts active devices through the real per-device endpoint', async ({ page }) => {
    const interruptRequests: string[] = [];

    await page.unroute('**/api/**');
    await page.route('**/api/**', async (route) => {
      const url = route.request().url();
      const method = route.request().method();

      if (url.endsWith('/api/v1/devices') && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(mockDevicesResponse),
        });
        return;
      }

      if (url.includes('/api/v1/devices/') && url.includes('/session') && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            task_id: null,
            instruction: null,
            mode: 'normal',
            status: 'idle',
            current_step: 0,
            max_steps: 100,
            latest_screenshot: null,
            interruptible: false,
          }),
        });
        return;
      }

      if (url.includes('/api/v1/devices/') && url.includes('/interrupt') && method === 'POST') {
        interruptRequests.push(url);
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ success: true }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.reload();
    await page.waitForLoadState('networkidle');

    await page.getByText('批处理').click();
    await page.getByRole('button', { name: /全\s*选/ }).click();
    await page.getByRole('button', { name: /下一步/ }).click();
    await page.getByPlaceholder('输入自然语言任务指令，例如：打开微信搜索附近的人').fill('打开设置并检查网络');
    await page.getByRole('button', { name: /开始执行/ }).click();

    await expect.poll(async () => {
      return page.evaluate(() => (window as any).__getLastMockWsReadyState());
    }).toBe(1);

    await page.getByRole('button', { name: '开始执行' }).click();

    await page.evaluate(({ deviceA, deviceB }) => {
      (window as any).__emitMockWsMessage({
        type: 'task_created',
        device_id: deviceA,
        task_id: 'task-batch-a',
        status: 'pending',
      });
      (window as any).__emitMockWsMessage({
        type: 'task_created',
        device_id: deviceB,
        task_id: 'task-batch-b',
        status: 'pending',
      });
      (window as any).__emitMockWsMessage({
        type: 'agent_status',
        device_id: deviceA,
        task_id: 'task-batch-a',
        status: 'running',
        step_number: 5,
        max_steps: 100,
        message: 'A running',
      });
      (window as any).__emitMockWsMessage({
        type: 'agent_status',
        device_id: deviceB,
        task_id: 'task-batch-b',
        status: 'running',
        step_number: 7,
        max_steps: 100,
        message: 'B running',
      });
    }, { deviceA: DEVICE_A, deviceB: DEVICE_B });

    await expect(page.getByRole('button', { name: '全部中断' })).toBeVisible();
    await page.getByRole('button', { name: '全部中断' }).click();

    await expect.poll(() => interruptRequests.length).toBe(2);
    expect(interruptRequests).toContain(`http://localhost:8000/api/v1/devices/${DEVICE_A}/interrupt`);
    expect(interruptRequests).toContain(`http://localhost:8000/api/v1/devices/${DEVICE_B}/interrupt`);

    await expect(page.getByText('已中断')).toHaveCount(2);
  });
});
