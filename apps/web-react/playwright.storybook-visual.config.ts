import { defineConfig, devices } from "@playwright/test";

const useStaticStorybook = process.env.SEEKTALENT_STORYBOOK_STATIC === "1";
const useExternalStorybook = process.env.SEEKTALENT_STORYBOOK_EXTERNAL === "1";
const storybookServerCommand = useStaticStorybook
  ? "python3 -m http.server 6006 --bind 127.0.0.1 --directory storybook-static >/tmp/seektalent-storybook-static-server.log 2>&1"
  : "pnpm exec storybook dev -p 6006 --host 127.0.0.1 --ci";
const storybookWebServer = useExternalStorybook
  ? {}
  : {
      webServer: {
        command: storybookServerCommand,
        reuseExistingServer: !process.env.CI && !useStaticStorybook,
        timeout: 120_000,
        url: "http://127.0.0.1:6006",
      },
    };

export default defineConfig({
  testDir: "./tests",
  testMatch: /storybook-visual\.spec\.ts/,
  timeout: 90_000,
  workers: process.env.CI ? 4 : undefined,
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
  ...storybookWebServer,
});
