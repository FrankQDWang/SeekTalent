import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import { chromium, type BrowserContext } from "playwright";

import { redactFixturePayload, REDACTED_VALUE } from "../src/redaction";
import { EncryptedSessionStore, type BrowserStorageState, type SessionScope } from "../src/sessionStore";

export type GateCommand = [string, ...string[]];

export type GateCheck = {
  name: string;
  ok: true;
};

export type CompatibilityGateResult = {
  ok: true;
  checks: GateCheck[];
};

export type CommandRunner = (command: GateCommand, options: { cwd: string }) => Promise<void>;

export type CompatibilityGateOptions = {
  cwd?: string;
  verifyProjectCommands?: boolean;
  verifyBrowserChecks?: boolean;
  commandRunner?: CommandRunner;
};

export type OpenDetailLikePageCommand = {
  type: "open-detail-like-page";
  url: string;
};

export type DetailLikePage = {
  goto(url: string): Promise<unknown>;
};

export type DetailLikePageContext<TPage extends DetailLikePage = DetailLikePage> = {
  newPage(): Promise<TPage>;
};

const WORKER_ROOT = new URL("..", import.meta.url).pathname;
const TEST_PAGE_URL =
  "data:text/html;charset=utf-8," +
  encodeURIComponent(`
    <!doctype html>
    <title>Compatibility Gate</title>
    <main id="status">ready</main>
    <script>
      fetch("https://compatibility.local/api/detail/123")
        .then((response) => response.json())
        .then((payload) => {
          document.querySelector("#status").textContent = payload.kind;
          window.__compatibilityDone = true;
        });
    </script>
  `);
const DETAIL_PAGE_URL =
  "data:text/html;charset=utf-8," +
  encodeURIComponent("<!doctype html><title>Candidate Detail</title><main>detail-like page</main>");

export async function runCompatibilityGate(
  options: CompatibilityGateOptions = {}
): Promise<CompatibilityGateResult> {
  const checks: GateCheck[] = [];
  const cwd = options.cwd ?? WORKER_ROOT;
  const commandRunner = options.commandRunner ?? runCommand;
  const verifyProjectCommands = options.verifyProjectCommands ?? true;
  const verifyBrowserChecks = options.verifyBrowserChecks ?? true;

  if (verifyProjectCommands) {
    await recordCheck(checks, "bun-ci", () => commandRunner(["bun", "ci"], { cwd }));
    await recordCheck(checks, "bun-test", () => commandRunner(["bun", "test"], { cwd }));
    await recordCheck(checks, "typecheck", () => commandRunner(["bun", "run", "typecheck"], { cwd }));
  }

  if (verifyBrowserChecks) {
    await runBrowserAndSessionChecks(checks);
  }

  return { ok: true, checks };
}

export function chromiumSetupFailureMessage(error: unknown): string {
  return `Playwright Chromium setup failed. Run \`bunx playwright install chromium\` from apps/liepin-worker. ${errorSummary(
    error
  )}`;
}

async function runBrowserAndSessionChecks(checks: GateCheck[]): Promise<void> {
  await recordCheck(checks, "playwright-chromium-installed", assertChromiumInstalled);
  await recordCheck(checks, "playwright-chromium-launch", assertChromiumLaunches);
  await assertPersistentContextFlow(checks);
  await assertEncryptedSessionFlow(checks);
  await recordCheck(checks, "redaction", assertRedactionPasses);
}

async function assertChromiumInstalled(): Promise<void> {
  const executablePath = chromium.executablePath();
  if (!existsSync(executablePath)) {
    throw new Error(chromiumSetupFailureMessage(new Error(`Chromium executable not found: ${executablePath}`)));
  }
}

async function assertChromiumLaunches(): Promise<void> {
  try {
    const browser = await chromium.launch({ headless: true });
    await browser.close();
  } catch (error) {
    throw new Error(chromiumSetupFailureMessage(error));
  }
}

async function assertPersistentContextFlow(checks: GateCheck[]): Promise<void> {
  const userDataDir = await mkdtemp(join(tmpdir(), "liepin-compat-profile-"));
  let context: BrowserContext | undefined;
  try {
    try {
      context = await chromium.launchPersistentContext(userDataDir, { headless: true });
    } catch (error) {
      throw new Error(chromiumSetupFailureMessage(error));
    }
    checks.push({ name: "persistent-context", ok: true });

    await context.route("https://compatibility.local/api/detail/123", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "access-control-allow-origin": "*" },
        body: JSON.stringify({ kind: "local-detail-response" }),
      })
    );

    const page = await context.newPage();
    const responseUrls: string[] = [];
    page.on("response", (response) => responseUrls.push(response.url()));

    await page.goto(TEST_PAGE_URL);
    if ((await page.title()) !== "Compatibility Gate") {
      throw new Error("Local compatibility test page did not load.");
    }
    checks.push({ name: "local-navigation", ok: true });

    await page.waitForFunction("window.__compatibilityDone === true");
    if (!responseUrls.includes("https://compatibility.local/api/detail/123")) {
      throw new Error("Local page response was not captured passively.");
    }
    checks.push({ name: "passive-response-capture", ok: true });

    const detailPage = await openDetailLikePageByCommand(context, {
      type: "open-detail-like-page",
      url: DETAIL_PAGE_URL,
    });
    if ((await detailPage.title()) !== "Candidate Detail") {
      throw new Error("Detail-like worker command did not open the expected page.");
    }
    checks.push({ name: "detail-command", ok: true });
  } finally {
    await context?.close();
    await rm(userDataDir, { recursive: true, force: true });
  }
}

export async function openDetailLikePageByCommand<TPage extends DetailLikePage>(
  context: DetailLikePageContext<TPage>,
  command: OpenDetailLikePageCommand
): Promise<TPage> {
  if (command.type !== "open-detail-like-page") {
    throw new Error(`Unsupported worker command: ${(command as { type?: string }).type ?? "missing"}`);
  }
  if (!isLocalTestUrl(command.url)) {
    throw new Error("Detail-like compatibility command requires a local test URL.");
  }
  const page = await context.newPage();
  await page.goto(command.url);
  return page;
}

function isLocalTestUrl(url: string): boolean {
  return url.startsWith("data:") || url.startsWith("file:");
}

async function assertEncryptedSessionFlow(checks: GateCheck[]): Promise<void> {
  const rootDir = await mkdtemp(join(tmpdir(), "liepin-compat-session-"));
  const scope = sessionScope();
  const state: BrowserStorageState = {
    cookies: [{ name: "compat_session", value: "session-secret", domain: "compatibility.local", path: "/" }],
    origins: [
      {
        origin: "https://compatibility.local",
        localStorage: [{ name: "compat-token", value: "local-secret" }],
      },
    ],
  };
  const store = new EncryptedSessionStore(rootDir, {
    keyId: "compatibility-gate",
    keyMaterial: `compat-${createHash("sha256").update(rootDir).digest("hex")}`,
  });

  try {
    await store.writeStorageState(scope, state);
    const reloaded = await store.readStorageState(scope);
    if (JSON.stringify(reloaded) !== JSON.stringify(state)) {
      throw new Error("Encrypted session state did not reload.");
    }
    checks.push({ name: "encrypted-session-reload", ok: true });

    try {
      await simulateWorkerCrashAfterEncryptedSessionWrite(store, scope, state);
    } catch (error) {
      if (!(error instanceof Error) || error.message !== "simulated-worker-crash") {
        throw error;
      }
    }
    await assertNoPlaintextSessionState(rootDir, ["session-secret", "local-secret", "compat_session"]);
    checks.push({ name: "crash-plaintext-check", ok: true });
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
}

async function simulateWorkerCrashAfterEncryptedSessionWrite(
  store: EncryptedSessionStore,
  scope: SessionScope,
  state: BrowserStorageState
): Promise<never> {
  await store.writeStorageState(scope, state);
  throw new Error("simulated-worker-crash");
}

async function assertNoPlaintextSessionState(rootDir: string, secrets: string[]): Promise<void> {
  for (const filePath of await listFiles(rootDir)) {
    if (basename(filePath) === "storage-state.json") {
      throw new Error(`Plaintext session state file was written: ${filePath}`);
    }
    const content = await readFile(filePath, "utf8");
    for (const secret of secrets) {
      if (content.includes(secret)) {
        throw new Error("Plaintext session state secret was written.");
      }
    }
  }
}

async function listFiles(rootDir: string): Promise<string[]> {
  const entries = await readdir(rootDir, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    const entryPath = join(rootDir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await listFiles(entryPath)));
    } else if (entry.isFile()) {
      files.push(entryPath);
    }
  }
  return files;
}

function assertRedactionPasses(): void {
  const result = redactFixturePayload({
    token: "secret-token",
    debugUrl: "ws://127.0.0.1:9222/devtools/browser/raw-token",
    candidate: {
      name: "Candidate Name",
      email: "person@example.com",
      note: "Reach at 13800138000",
    },
  });

  if (!result.manifest.redaction_passed || result.manifest.unsafe_reasons.length !== 0) {
    throw new Error("Redaction manifest did not pass.");
  }
  const serialized = JSON.stringify(result.payload);
  if (serialized.includes("secret-token") || serialized.includes("person@example.com") || serialized.includes("13800138000")) {
    throw new Error("Sensitive fixture data was not redacted.");
  }
  if (!serialized.includes(REDACTED_VALUE)) {
    throw new Error("Redaction did not mark sensitive values.");
  }
}

function sessionScope(): SessionScope {
  return {
    tenantId: "tenant-a",
    workspaceId: "workspace-a",
    providerAccountHash: "account-hash-a",
    connectionId: "conn-compat",
  };
}

async function recordCheck(checks: GateCheck[], name: string, run: () => Promise<void> | void): Promise<void> {
  try {
    await run();
    checks.push({ name, ok: true });
  } catch (error) {
    throw new Error(`${name} failed: ${errorSummary(error)}`);
  }
}

async function runCommand(command: GateCommand, options: { cwd: string }): Promise<void> {
  const process = Bun.spawnSync(command, {
    cwd: options.cwd,
    stdout: "pipe",
    stderr: "pipe",
  });

  if (process.exitCode !== 0) {
    const stdout = process.stdout.toString("utf8");
    const stderr = process.stderr.toString("utf8");
    const output = [stderr, stdout].filter(Boolean).join("\n");
    throw new Error(
      `${command.join(" ")} exited with ${process.exitCode}\n${sanitizeOutput(output).slice(0, 4000)}`
    );
  }
}

function errorSummary(error: unknown): string {
  if (error instanceof Error) {
    return sanitizeOutput(error.message);
  }
  return sanitizeOutput(String(error));
}

function sanitizeOutput(value: string): string {
  const lines = value.split(/\r?\n/).map((line) => {
    const headerMatch = line.match(/^\s*(cookie|authorization)\s*:\s*(.*)$/i);
    if (headerMatch) {
      return `${headerMatch[1]}: ${REDACTED_VALUE}`;
    }

    const storageMatch = line.match(/^\s*(storageState)\s*=\s*(.*)$/i);
    if (storageMatch) {
      return `${storageMatch[1]}=${REDACTED_VALUE}`;
    }

    const redacted = redactFixturePayload({ output: line }).payload as { output: string };
    return redacted.output;
  });

  return lines.join("\n");
}

if (import.meta.main) {
  try {
    const result = await runCompatibilityGate();
    for (const check of result.checks) {
      console.log(`OK ${check.name}`);
    }
    console.log("Liepin Bun compatibility gate passed.");
  } catch (error) {
    console.error(`Liepin Bun compatibility gate failed: ${errorSummary(error)}`);
    process.exit(1);
  }
}
