import { describe, expect, it } from "bun:test";

import {
  chromiumSetupFailureMessage,
  runCompatibilityGate,
  type GateCommand,
} from "../scripts/compatibilityGate";

describe("liepin bun compatibility gate", () => {
  it("runs local Playwright session checks without contacting Liepin", async () => {
    const result = await runCompatibilityGate({
      verifyProjectCommands: false,
      commandRunner: async (command: GateCommand) => {
        throw new Error(`unexpected shell command: ${command.join(" ")}`);
      },
    });

    expect(result.ok).toBe(true);
    expect(result.checks.map((check) => check.name)).toEqual([
      "playwright-chromium-installed",
      "playwright-chromium-launch",
      "persistent-context",
      "local-navigation",
      "passive-response-capture",
      "detail-command",
      "encrypted-session-reload",
      "crash-plaintext-check",
      "redaction",
    ]);
    expect(JSON.stringify(result)).not.toContain("liepin.com");
    expect(JSON.stringify(result)).not.toContain("devtools");
  });

  it("verifies the lockfile, Bun tests, and typecheck through project commands", async () => {
    const commands: GateCommand[] = [];

    const result = await runCompatibilityGate({
      verifyBrowserChecks: false,
      commandRunner: async (command: GateCommand) => {
        commands.push(command);
      },
    });

    expect(result.ok).toBe(true);
    expect(commands).toEqual([["bun", "ci"], ["bun", "test"], ["bun", "run", "typecheck"]]);
    expect(result.checks.map((check) => check.name)).toEqual(["bun-ci", "bun-test", "typecheck"]);
  });

  it("opens detail-like pages through a named worker command contract", async () => {
    const module = await import("../scripts/compatibilityGate");
    expect(module.openDetailLikePageByCommand).toBeFunction();

    const visitedUrls: string[] = [];
    const page = {
      goto: async (url: string) => {
        visitedUrls.push(url);
      },
      title: async () => "Candidate Detail",
    };
    const context = {
      newPage: async () => page,
    };
    const command = {
      type: "open-detail-like-page",
      url: "data:text/html;charset=utf-8,%3Ctitle%3ECandidate%20Detail%3C%2Ftitle%3E",
    } as const;

    const openedPage = await module.openDetailLikePageByCommand(context, command);

    expect(openedPage).toBe(page);
    expect(visitedUrls).toEqual([command.url]);
  });

  it("redacts failed command output before surfacing setup errors", async () => {
    const unsafeOutput = [
      "GET https://www.liepin.com/?token=liepin-url-secret",
      "GET https://www.liepin.com/candidate/123",
      "retry token=plain-token",
      "Cookie: lt_auth=cookie-secret; sid=session-secret",
      'storageState={"cookies":[{"value":"storage-secret"}]}',
      'localStorage={"token":"local-storage-secret"}',
      'sessionStorage={"token":"session-storage-secret"}',
      "auth: bearer-secret",
      "Authorization: Bearer auth-secret",
      "ws://127.0.0.1:9222/devtools/browser/debug-secret",
      "http://127.0.0.1:9222/json/version",
      JSON.stringify({
        headers: {
          cookie: "sid=json-session-secret",
          authorization: "Bearer json-auth-secret",
        },
        storageState: {
          cookies: [{ name: "sid", value: "json-storage-secret" }],
          origins: [
            {
              origin: "https://www.liepin.com",
              localStorage: [{ name: "token", value: "json-local-storage-secret" }],
              sessionStorage: [{ name: "token", value: "json-session-storage-secret" }],
            },
          ],
        },
      }),
    ].join("\n");

    let message = "";
    try {
      await runCompatibilityGate({
        verifyBrowserChecks: false,
        commandRunner: async () => {
          throw new Error(unsafeOutput);
        },
      });
    } catch (error) {
      message = error instanceof Error ? error.message : String(error);
    }

    expect(message).not.toContain("https://www.liepin.com/?token=liepin-url-secret");
    expect(message).not.toContain("https://www.liepin.com/candidate/123");
    expect(message).not.toContain("plain-token");
    expect(message).not.toContain("cookie-secret");
    expect(message).not.toContain("storage-secret");
    expect(message).not.toContain("local-storage-secret");
    expect(message).not.toContain("session-storage-secret");
    expect(message).not.toContain("bearer-secret");
    expect(message).not.toContain("auth-secret");
    expect(message).not.toContain("debug-secret");
    expect(message).not.toContain("http://127.0.0.1:9222/json/version");
    expect(message).not.toContain("json-session-secret");
    expect(message).not.toContain("json-auth-secret");
    expect(message).not.toContain("json-storage-secret");
    expect(message).not.toContain("json-local-storage-secret");
    expect(message).not.toContain("json-session-storage-secret");
    expect(message).toContain("[REDACTED]");
  });

  it("names the Chromium setup command when browser binaries are missing", () => {
    expect(chromiumSetupFailureMessage(new Error("Executable does not exist"))).toContain(
      "bunx playwright install chromium"
    );
  });
});
