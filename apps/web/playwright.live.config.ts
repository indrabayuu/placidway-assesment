import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'live.integration.spec.ts',
  timeout: 180_000,
  reporter: 'list',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:5173',
    browserName: 'chromium',
    launchOptions: { channel: 'chrome' },
    headless: true,
  },
});
