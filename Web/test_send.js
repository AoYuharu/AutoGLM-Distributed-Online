const { chromium } = require('playwright');
(async () => {
  try {
    const browser = await chromium.launch({
      headless: false,
      executablePath: 'C:/Users/Lenovo/AppData/Local/ms-playwright/chromium-1208/chrome-win64/chrome.exe'
    });
    const page = await browser.newPage();

    page.on('console', msg => {
      const text = msg.text().substring(0, 200);
      if (msg.type() === 'error' && !text.includes('Warning:') && !text.includes('deprecated')) {
        console.log('ERROR:', text.substring(0, 200));
      }
      if (text.includes('agent_step') || text.includes('你好')) {
        console.log('AGENT:', msg.type().toUpperCase(), text.substring(0, 100));
      }
    });

    console.log('Opening http://localhost:5173...');
    await page.goto('http://localhost:5173', { timeout: 30000 });
    await page.waitForTimeout(5000);

    // Click on device
    const device = page.locator('text=10AE551838000D7').first();
    await device.click({ timeout: 5000 });
    await page.waitForTimeout(2000);

    // Type message
    const textarea = page.locator('textarea').first();
    await textarea.fill('你好');
    console.log('Typed: 你好');

    // Click send button
    const sendBtn = page.locator('button:has-text("发送")');
    await sendBtn.click();
    console.log('Clicked send');

    // Wait for response
    await page.waitForTimeout(10000);

    // Take screenshot
    await page.screenshot({ path: 'D:/MyProject/Programming/Open-AutoGLM/Distributed/test_result.png', fullPage: true });
    console.log('Screenshot saved');

    await browser.close();
  } catch (e) {
    console.error('Error:', e.message);
  }
})();
