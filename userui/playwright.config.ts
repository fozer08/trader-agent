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
      USERUI_HOST: '127.0.0.1',
      USERUI_PORT: '4173',
      USERUI_ORIGIN: '',
      ENGINE_URL: 'http://127.0.0.1:1',
      ENGINE_API_KEY: 'test-key',
      USERUI_DATABASE_PATH: ':memory:',
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
