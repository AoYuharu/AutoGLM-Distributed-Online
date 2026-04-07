const { chromium } = require('playwright');
(async () => {
  try {
    const browser = await chromium.launch({
      headless: false,
      executablePath: 'C:/Users/Lenovo/AppData/Local/ms-playwright/chromium-1208/chrome-win64/chrome.exe'
    });
    const page = await browser.newPage();

    page.on('console', msg => {
      const text = msg.text();
      const type = msg.type();
      if (type === 'error' || text.includes('sendCommand') || text.includes('error') || text.includes('ERROR') || text.includes('agent')) {
        console.log(`[${type}]`, text.substring(0, 300));
      }
    });
    page.on('pageerror', err => {
      console.log('[PAGEERROR]', err.message.substring(0, 300));
    });

    console.log('Opening http://localhost:5173...');
    await page.goto('http://localhost:5173', { timeout: 30000 });
    await page.waitForTimeout(5000);

    // Click on device
    const device = page.locator('text=10AE551838000D7').first();
    await device.click({ timeout: 5000 });
    await page.waitForTimeout(3000);

    // Check textarea status
    const textarea = page.locator('textarea').first();
    const isDisabled = await textarea.isDisabled();
    console.log('Textarea disabled:', isDisabled);

    // Type message
    if (!isDisabled) {
      await textarea.fill('你好');
      console.log('Typed: 你好');

      // Click send
      const sendBtn = page.locator('button:has-text("发送")');
      await sendBtn.click();
      console.log('Clicked send');
    } else {
      console.log('Textarea is disabled, cannot send');
    }

    // Wait for response
    await page.waitForTimeout(5000);

    await page.screenshot({ path: 'D:/MyProject/Programming/Open-AutoGLM/Distributed/test_debug.png', fullPage: true });
    console.log('Screenshot saved');

    await browser.close();
  } catch (e) {
    console.error('Error:', e.message);
  }
})();
