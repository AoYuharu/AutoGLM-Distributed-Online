import { defineConfig, devices } from '@playwright/test';
import { getWebDevUrl, loadSharedWebConfig } from './sharedConfig';

const sharedConfig = loadSharedWebConfig();
const webBaseUrl = getWebDevUrl(sharedConfig);

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  expect: {
    timeout: 5000,
  },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: webBaseUrl,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: webBaseUrl,
    reuseExistingServer: true,
    timeout: 30000,
  },
});

