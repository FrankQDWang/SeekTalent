import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: /storybook-visual\.spec\.ts/,
  timeout: 90_000,
  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      maxDiffPixelRatio: 0.01,
    },
  },
  use: {
    baseURL: "http://127.0.0.1:6006",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "mobile-375",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { height: 812, width: 375 },
      },
    },
    {
      name: "tablet-768",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { height: 1024, width: 768 },
      },
    },
    {
      name: "desktop-1440",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { height: 960, width: 1440 },
      },
    },
    {
      name: "desktop-wide",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { height: 1080, width: 1920 },
      },
    },
  ],
  webServer: {
    command: "pnpm exec storybook dev -p 6006 --host 127.0.0.1 --ci",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    url: "http://127.0.0.1:6006",
  },
});
