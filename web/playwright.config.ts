import { defineConfig, devices } from "@playwright/test";

const chromium = process.env.CHROMIUM_PATH;

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://localhost:8000",
    launchOptions: chromium ? { executablePath: chromium } : {},
  },
  webServer: {
    command: "wms serve --demo --fresh",
    cwd: "..",
    url: "http://localhost:8000/api/health/ready",
    timeout: 600_000,
    reuseExistingServer: !process.env.CI,
  },
});
