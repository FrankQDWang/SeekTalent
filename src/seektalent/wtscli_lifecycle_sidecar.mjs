import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

const args = parseArgs(process.argv.slice(2));
const packageDir = requiredArg("package-dir");
const statusPath = requiredArg("status-path");
const lockPath = requiredArg("lock-path");
const bridgeBuildId = requiredArg("bridge-build-id");
const controlPath = optionalArg("control-path");
const parentPid = optionalIntegerArg("parent-pid");
const lifecycleId = optionalArg("lifecycle-id");
const supervisorRestartCount = optionalIntegerArg("supervisor-restart-count") ?? 0;
const supervisorFirstFailureCode = optionalArg("supervisor-first-failure-code");
const maxRestarts = 3;
const pollMilliseconds = 500;
const restartDelayMilliseconds = 250;

const lifecycle = await import(
  pathToFileURL(path.join(packageDir, "dist/src/browser/daemon-lifecycle.js")).href
);
const ownership = await import(
  pathToFileURL(path.join(packageDir, "dist/src/browser/daemon-ownership.js")).href
);
const transport = await import(
  pathToFileURL(path.join(packageDir, "dist/src/browser/daemon-transport.js")).href
);

let child = null;
let daemonOwned = false;
let daemonToken = null;
let stopping = false;
let restartCount = 0;
let firstFailureCode = null;
let pendingRestart = null;
let attentionReason = null;
let lastHealth = { state: "stopped", status: null };
let childStartedAt = null;

claimSupervisorLock();
writeStatus({ state: "warming", status: null });
process.on("SIGTERM", () => void shutdown());
process.on("SIGINT", () => void shutdown());

try {
  await ensureDaemon();
  await monitor();
} catch (error) {
  await stopOwnedChild();
  if (daemonToken) {
    ownership.removeDaemonOwnershipRecord(daemonToken);
    daemonToken = null;
  }
  markNeedsAttention(safeReason(error, "wtscli_supervisor_start_failed"));
  releaseSupervisorLock();
  process.exitCode = 70;
}

async function ensureDaemon() {
  lastHealth = await inspectHealth();
  if (lastHealth.state === "foreign") {
    throw new Error("wtscli_foreign_owner");
  }
  if (lastHealth.state !== "stopped") {
    throw new Error("wtscli_daemon_existing_without_owner");
  }
  const prepared = ownership.prepareDaemonOwnership();
  if (!prepared) {
    lastHealth = await inspectHealth();
    if (lastHealth.state === "foreign") {
      throw new Error("wtscli_foreign_owner");
    }
    if (lastHealth.state !== "stopped") {
      throw new Error("wtscli_daemon_existing_without_owner");
    }
    throw new Error("wtscli_daemon_owner_busy");
  }
  daemonToken = prepared.token;
  const launch = lifecycle.resolveDaemonLaunchSpec();
  try {
    child = spawn(launch.binary, launch.args, {
      detached: false,
      stdio: "ignore",
      env: {
        ...process.env,
        [ownership.DAEMON_OWNERSHIP_TOKEN_ENV]: prepared.token,
      },
    });
    ownership.bindDaemonOwnershipPid?.(daemonToken, child.pid);
  } catch (error) {
    await stopOwnedChild();
    ownership.removeDaemonOwnershipRecord(daemonToken);
    daemonToken = null;
    throw error;
  }
  daemonOwned = true;
  childStartedAt = Date.now();
  child.once("error", (error) => handleChildExit(null, error));
  child.once("exit", (code, signal) => handleChildExit(code, signal));
  writeStatus({ state: "warming", status: null });
}

async function monitor() {
  while (!stopping) {
    if (controlRequestsShutdown()) {
      await shutdown();
      return;
    }
    if (parentPid !== null && !processAlive(parentPid)) {
      await shutdown();
      return;
    }
    if (attentionReason !== null) {
      writeStatus({ state: "needs_attention", status: null });
      await sleep(pollMilliseconds);
      continue;
    }
    lastHealth = await inspectHealth();
    if (lastHealth.state === "foreign") {
      markNeedsAttention("wtscli_foreign_owner");
    } else if (lastHealth.state === "stopped" && child !== null && child.exitCode === null) {
      // A live child without its listener is not healthy. Give startup one
      // bounded heartbeat, then terminate only the child this owner spawned.
      if (daemonOwned) {
        if (childStartedAt !== null && Date.now() - childStartedAt >= 5000) {
          await stopOwnedChild();
        } else {
          writeStatus({ state: "warming", status: null });
        }
      }
    } else {
      writeStatus(lastHealth);
    }
    await sleep(pollMilliseconds);
  }
}

async function inspectHealth() {
  try {
    const health = await transport.getDaemonHealth({ timeout: 1000 });
    if (health.status?.bridgeBuildId !== undefined && health.status.bridgeBuildId !== bridgeBuildId) {
      return { state: "foreign", status: null };
    }
    return health;
  } catch {
    return { state: "foreign", status: null };
  }
}

function handleChildExit(code, signal) {
  if (stopping || child === null) return;
  child = null;
  childStartedAt = null;
  daemonOwned = false;
  if (daemonToken) {
    ownership.removeDaemonOwnershipRecord(daemonToken);
    daemonToken = null;
  }
  restartCount += 1;
  if (firstFailureCode === null) {
    firstFailureCode = safeExitCode(code, signal);
  }
  if (restartCount > maxRestarts) {
    markNeedsAttention("wtscli_daemon_restart_budget_exhausted");
    return;
  }
  writeStatus({ state: "warming", status: null });
  pendingRestart = setTimeout(() => {
    pendingRestart = null;
    void ensureDaemon().catch((error) => {
      markNeedsAttention(safeReason(error, "wtscli_daemon_restart_failed"));
    });
  }, restartDelayMilliseconds * restartCount);
}

async function shutdown() {
  if (stopping) return;
  stopping = true;
  if (pendingRestart !== null) clearTimeout(pendingRestart);
  pendingRestart = null;
  await stopOwnedChild();
  if (daemonToken) {
    ownership.removeDaemonOwnershipRecord(daemonToken);
    daemonToken = null;
  }
  attentionReason = null;
  writeStatus({ state: "stopped", status: null });
  releaseSupervisorLock();
  process.exit(0);
}

async function stopOwnedChild() {
  const ownedChild = child;
  child = null;
  childStartedAt = null;
  const hadOwnership = daemonOwned;
  daemonOwned = false;
  if (ownedChild === null) return;
  if (hadOwnership) {
    const accepted = await transport.requestDaemonShutdown({ timeout: 3000 }).catch(() => false);
    if (!accepted && ownedChild.exitCode === null) ownedChild.kill("SIGTERM");
  }
  await waitForExit(ownedChild, 3000);
  if (ownedChild.exitCode === null) ownedChild.kill("SIGKILL");
}

function markNeedsAttention(reasonCode) {
  attentionReason = reasonCode;
  writeStatus({ state: "needs_attention", status: null, reasonCode });
}

function writeStatus(health) {
  const state = attentionReason !== null
    ? "needs_attention"
    : health.state === "ready"
    ? "ready"
    : health.state === "no-extension"
      ? "extension_not_connected"
      : health.state === "profile-disconnected" || health.state === "profile-required"
        ? "profile_not_connected"
        : health.state === "needs_attention"
          ? "needs_attention"
          : health.state === "stopped"
            ? (stopping ? "stopped" : "warming")
            : "warming";
  const status = health.status ?? {};
  const payload = {
    schemaVersion: "seektalent.wtscli_supervisor_status.v1",
    state,
    bridgeBuildId,
    lifecycleId,
    supervisorPid: process.pid,
    daemonPid: child?.pid ?? (daemonOwned && typeof status.pid === "number" ? status.pid : null),
    daemonOwned,
    processHealthy: state === "ready" || state === "extension_not_connected" || state === "profile_not_connected",
    extensionConnected: state === "ready",
    restartCount,
    firstFailureCode,
    supervisorRestartCount,
    supervisorFirstFailureCode,
    reasonCode: health.reasonCode ?? attentionReason ?? null,
    observedAt: new Date().toISOString(),
  };
  atomicWrite(statusPath, payload);
}

function claimSupervisorLock() {
  const payload = {
    schemaVersion: "seektalent.wtscli_supervisor_lock.v1",
    supervisorPid: process.pid,
    bridgeBuildId,
    lifecycleId,
    createdAt: new Date().toISOString(),
  };
  try {
    fs.mkdirSync(path.dirname(lockPath), { recursive: true, mode: 0o700 });
    const handle = fs.openSync(lockPath, "wx", 0o600);
    fs.writeFileSync(handle, `${JSON.stringify(payload)}\n`);
    fs.closeSync(handle);
    return;
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
    const existing = readJsonStrict(lockPath);
    if (existing?.bridgeBuildId !== bridgeBuildId || processAlive(existing?.supervisorPid)) {
      throw new Error("wtscli_supervisor_foreign_owner");
    }
    fs.rmSync(lockPath, { force: true });
    claimSupervisorLock();
  }
}

function releaseSupervisorLock() {
  const existing = readJson(lockPath);
  if (existing?.supervisorPid === process.pid) fs.rmSync(lockPath, { force: true });
}

function controlRequestsShutdown() {
  if (controlPath === null) return false;
  const request = readJson(controlPath);
  if (request?.command !== "shutdown") return false;
  if (lifecycleId !== null && request.lifecycleId !== lifecycleId) return false;
  fs.rmSync(controlPath, { force: true });
  return true;
}

function atomicWrite(target, value) {
  const temporary = `${target}.tmp-${process.pid}-${Date.now()}`;
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  fs.writeFileSync(temporary, `${JSON.stringify(value)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
  fs.renameSync(temporary, target);
}

function readJson(target) {
  try { return JSON.parse(fs.readFileSync(target, "utf8")); } catch { return null; }
}

function readJsonStrict(target) {
  try {
    return JSON.parse(fs.readFileSync(target, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw new Error("wtscli_supervisor_foreign_owner");
  }
}

function processAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch { return false; }
}

function safeExitCode(code, signal) {
  if (typeof code === "number" && Number.isInteger(code)) return `wtscli_daemon_exit_${code}`;
  if (typeof signal === "string" && /^[A-Z0-9_]+$/.test(signal)) return `wtscli_daemon_${signal.toLowerCase()}`;
  return "wtscli_daemon_exit_unknown";
}

function safeReason(error, fallback) {
  const value = error instanceof Error ? error.message : String(error);
  return /^[a-z][a-z0-9_]{0,159}$/.test(value) ? value : fallback;
}

function waitForExit(processHandle, timeoutMilliseconds) {
  if (processHandle.exitCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(() => { processHandle.removeListener("exit", done); resolve(); }, timeoutMilliseconds);
    const done = () => { clearTimeout(timer); resolve(); };
    processHandle.once("exit", done);
  });
}

function sleep(milliseconds) { return new Promise((resolve) => setTimeout(resolve, milliseconds)); }

function parseArgs(values) {
  const parsed = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    const value = values[index + 1];
    if (!key?.startsWith("--") || !value || parsed[key.slice(2)] !== undefined) {
      throw new Error("wtscli_supervisor_arguments_invalid");
    }
    parsed[key.slice(2)] = value;
  }
  return parsed;
}

function requiredArg(name) {
  const value = args[name];
  if (typeof value !== "string" || value.length === 0) throw new Error("wtscli_supervisor_arguments_invalid");
  return value;
}

function optionalArg(name) {
  const value = args[name];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function optionalIntegerArg(name) {
  const value = optionalArg(name);
  if (value === null) return null;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 0) throw new Error("wtscli_supervisor_arguments_invalid");
  return number;
}
