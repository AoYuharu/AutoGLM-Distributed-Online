import { test, expect } from '@playwright/test';

test.describe('Agent Parse Retry Mechanism', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('http://localhost:5173');
  });

  test('web interface loads correctly', async ({ page }) => {
    // Wait for page to load
    await page.waitForLoadState('networkidle');

    // Check title
    const title = await page.title();
    console.log('Page title:', title);

    // Check if root element has content
    const root = page.locator('#root');
    await expect(root).toBeVisible();

    // Get body text to see what's rendered
    const bodyText = await page.textContent('body');
    console.log('Body content preview:', bodyText?.substring(0, 300));
  });

  test('server health check', async ({ request }) => {
    const response = await request.get('http://localhost:8000/health');
    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    expect(data.status).toBe('healthy');
    console.log('Health check response:', data);
  });
});