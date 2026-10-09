import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    launchOptions: process.env.TRADER_BROWSER_PATH
      ? { executablePath: process.env.TRADER_BROWSER_PATH }
      : {},
  },
  webServer: {
    command: 'node server.mjs',
    url: 'http://127.0.0.1:4173/health',
    env: {
      WEB_HOST: '127.0.0.1',
      WEB_PORT: '4173',
      WEB_PASSWORD: '',
      WEB_ORIGIN: '',
      ENGINE_URL: 'http://127.0.0.1:1',
      ENGINE_API_KEY: '',
    },
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1080 } },
    },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
});
