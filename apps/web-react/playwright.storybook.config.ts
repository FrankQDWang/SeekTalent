import { defineConfig, devices } from "@playwright/test";

const useStaticStorybook = process.env.SEEKTALENT_STORYBOOK_STATIC === "1";
const useExternalStorybook = process.env.SEEKTALENT_STORYBOOK_EXTERNAL === "1";
const storybookBaseURL =
  process.env.SEEKTALENT_STORYBOOK_BASE_URL ?? "http://127.0.0.1:6006";
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
        url: storybookBaseURL,
      },
    };

export default defineConfig({
  testDir: "./tests",
  testMatch: /storybook-(a11y|interactions)\.spec\.ts/,
  timeout: 60_000,
  workers: process.env.CI ? 4 : undefined,
  use: {
    baseURL: storybookBaseURL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "desktop-1440",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { height: 960, width: 1440 },
      },
    },
  ],
  ...storybookWebServer,
});
