import { test, expect } from '@playwright/test';

const DEVICE_ID = 'test-device-log-001';
const PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WnR0i8AAAAASUVORK5CYII=';

const mockDevicesResponse = {
  devices: [
    {
      id: '1',
      device_id: DEVICE_ID,
      client_id: 'client-1',
      platform: 'android',
      device_name: 'Pixel Test',
      os_version: '14',
      screen_width: 1080,
      screen_height: 2400,
      status: 'idle',
      connection: 'usb',
      last_seen: '2026-04-08T16:34:23.000Z',
      current_task_id: null,
      current_app: null,
      remark: 'log test device',
    },
  ],
};

const mockHistoryResponse = {
  device_id: DEVICE_ID,
  react_records: [
    {
      step_number: 1,
      phase: 'reason',
      reasoning: '分析任务并决定打开设置。',
      task_id: 'task_latest_001',
      timestamp: '2026-04-08T16:34:20.000Z',
    },
    {
      step_number: 1,
      phase: 'observe',
      observation: '已经打开设置页面。',
      screenshot: 'screenshots/step_1_20260408_163421.png',
      success: true,
      task_id: 'task_latest_001',
      timestamp: '2026-04-08T16:34:21.000Z',
    },
  ],
  chat_history: [
    {
      id: 'msg_task_latest_001_user',
      role: 'user',
      content: '打开设置，把熄屏时间调为10分钟',
      created_at: '2026-04-08T16:34:19.000Z',
    },
    {
      id: 'msg_agent_1',
      role: 'agent',
      content: '收到，开始执行。',
      created_at: '2026-04-08T16:34:19.500Z',
      task_id: 'task_latest_001',
    },
  ],
  screenshots: ['step_1_20260408_163421.png'],
};

const mockArtifactsResponse = {
  device_id: DEVICE_ID,
  screenshots: ['step_1_20260408_163421.png'],
  latest_screenshot: 'latest.png',
  latest_screenshot_download: `/api/v1/devices/${DEVICE_ID}/artifacts/screenshot/latest`,
  latest_log_download: `/api/v1/devices/${DEVICE_ID}/artifacts/logs/latest`,
  react_records_download: `/api/v1/devices/${DEVICE_ID}/artifacts/react-records`,
  chat_history_download: `/api/v1/devices/${DEVICE_ID}/artifacts/chat-history`,
};

const mockLatestLogText = [
  JSON.stringify({
    type: 'observe_result',
    task_id: 'task_latest_001',
    step_number: 1,
    success: true,
    result: 'ActionResult(success=True)',
    screenshot: 'screenshots/step_1_20260408_163421.png',
    timestamp: '2026-04-08T16:34:22.000Z',
    date: '2026-04-08',
  }),
].join('\n');

test.describe('Log panel timeline', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/**', async (route) => {
      const url = route.request().url();

      if (url.includes('/health')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'healthy' }),
        });
        return;
      }

      if (url.endsWith('/api/v1/devices')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(mockDevicesResponse),
        });
        return;
      }

      if (url.includes(`/api/v1/devices/${DEVICE_ID}/history`)) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(mockHistoryResponse),
        });
        return;
      }

      if (url.includes(`/api/v1/devices/${DEVICE_ID}/artifacts`) && !url.includes('/artifacts/file') && !url.includes('/logs/latest') && !url.includes('/screenshot/latest') && !url.includes('/react-records') && !url.includes('/chat-history')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(mockArtifactsResponse),
        });
        return;
      }

      if (url.includes(`/api/v1/devices/${DEVICE_ID}/artifacts/logs/latest`)) {
        await route.fulfill({
          status: 200,
          contentType: 'text/plain',
          body: mockLatestLogText,
        });
        return;
      }

      if (url.includes(`/api/v1/devices/${DEVICE_ID}/artifacts/file?path=`) || url.includes(`/api/v1/devices/${DEVICE_ID}/artifacts/screenshot/latest`)) {
        await route.fulfill({
          status: 200,
          contentType: 'image/png',
          body: Buffer.from(PNG_BASE64, 'base64'),
        });
        return;
      }

      if (url.includes(`/api/v1/devices/${DEVICE_ID}/artifacts/react-records`) || url.includes(`/api/v1/devices/${DEVICE_ID}/artifacts/chat-history`)) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true }),
        });
        return;
      }

      if (url.includes('/api/v1/tasks/devices/')) {
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

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.route('**/ws/**', async (route) => {
      await route.abort();
    });

    await page.goto('http://localhost:5173');
    await page.waitForLoadState('networkidle');
  });

  test('loads merged timeline and opens screenshot preview', async ({ page }) => {
    await page.getByRole('button', { name: '日志' }).click();

    await expect(page.getByText(`设备归档日志 - ${DEVICE_ID}`)).toBeVisible();
    await expect(page.getByText('最近 task_id: task_latest_001')).toBeVisible();
    await expect(page.getByText('打开设置，把熄屏时间调为10分钟')).toBeVisible();
    await expect(page.getByText('收到，开始执行。')).toBeVisible();
    await expect(page.getByText('分析任务并决定打开设置。')).toBeVisible();
    await expect(page.getByText('ActionResult(success=True)')).toBeVisible();

    const screenshotRow = page.locator('tr', { hasText: 'ActionResult(success=True)' });
    await screenshotRow.getByRole('button', { name: '查看截图' }).click();
    const previewDialog = page.getByRole('dialog').filter({ has: page.getByAltText('日志截图') });
    await expect(previewDialog.getByAltText('日志截图')).toBeVisible();
    await previewDialog.getByRole('button', { name: 'Close' }).click();
    await expect(previewDialog).toBeHidden();
  });

  test('filters current task timeline and exposes raw downloads', async ({ page }) => {
    await page.getByRole('button', { name: '日志' }).click();
    await expect(page.getByRole('button', { name: '最新日志' })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'ReAct 记录' })).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Chat History' })).toBeEnabled();
    await expect(page.getByRole('button', { name: '最新截图' })).toBeEnabled();

    await page.getByPlaceholder('搜索消息 / 类型 / task_id / details').fill('ActionResult');
    await expect(page.getByText('ActionResult(success=True)')).toBeVisible();
    await expect(page.getByText('收到，开始执行。')).not.toBeVisible();

    const [jsonDownload] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'JSON' }).click(),
    ]);
    expect(jsonDownload.suggestedFilename()).toContain(`device_timeline_${DEVICE_ID}`);
  });
});
