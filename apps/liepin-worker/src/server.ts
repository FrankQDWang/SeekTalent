import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { isIP } from "node:net";
import { chromium, type BrowserContextOptions } from "playwright";

import { searchCards, type CardSearchRequestBody } from "./cardSearch";
import { WORKER_CONTRACT_VERSION } from "./contracts";
import { openDetails } from "./detail";
import {
  LoginRelayNotVerifiedError,
  PlaywrightLoginRelayController,
  type LoginRelayController,
  type LoginRelayInputRequest,
} from "./loginRelay";
import { createInternalLoginHandoff } from "./session";
import { EncryptedSessionStore, loadSessionStoreKeyFromEnv, type SessionScope } from "./sessionStore";

type SessionStatusResponse = {
  connectionId: string;
  status: "missing" | "login_required" | "ready" | "revoked";
  providerAccountHash?: string;
  fixtureOnly: boolean;
};

type WorkerFetchOptions = {
  authToken: string;
  sessionStore?: EncryptedSessionStore;
  sessionStatus?: SessionStatusResponse;
  cardSearchHandler?: (body: CardSearchRequestBody) => Promise<object>;
  detailOpenKeyApproved?: (body: DetailOpenRequestBody, request: DetailOpenRequestBody["requests"][number]) => boolean;
  detailOpenHandler?: (body: DetailOpenRequestBody) => Promise<object>;
  handoffTokenFactory?: () => string;
  loginRelay?: LoginRelayController;
  now?: () => Date;
};

type DetailOpenRequestBody = {
  tenantId?: string;
  workspaceId?: string;
  providerAccountHash?: string;
  connectionId?: string;
  providerDayKey?: string;
  workerCommandId: string;
  requests: Array<{
    requestId: string;
    attemptId: string;
    idempotencyKey: string;
    approvalKey?: string;
    candidateId: string;
    detailUrl?: string;
  }>;
};

const DEFAULT_SESSION_STATUS: SessionStatusResponse = {
  connectionId: "default",
  status: "login_required",
  fixtureOnly: false,
};

export function createWorkerFetchHandler(options: WorkerFetchOptions): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/internal/")) {
      return json({ error: { code: "not_found" } }, 404);
    }

    const authResponse = authorize(request, options.authToken);
    if (authResponse !== null) {
      return authResponse;
    }

    try {
      if (request.method === "GET" && url.pathname === "/internal/health") {
        return json({ status: "ok", workerVersion: WORKER_CONTRACT_VERSION });
      }

      if (request.method === "GET" && url.pathname === "/internal/session/status") {
        const connectionId = url.searchParams.get("connectionId") ?? options.sessionStatus?.connectionId ?? "default";
        return json({ ...(await statusFor(options, connectionId, sessionScopeFromQuery(url))) });
      }

      if (request.method === "POST" && url.pathname === "/internal/session/login-handoff") {
        const body = await readJsonObject(request);
        const connectionId = stringValue(body.connectionId, "connectionId");
        const now = options.now?.() ?? new Date();
        const expiresAt = new Date(now.getTime() + 5 * 60 * 1000);
        const handoffToken = options.handoffTokenFactory?.() ?? randomUUID();
        if (options.loginRelay !== undefined) {
          const relayRequest = {
            connectionId,
            handoffToken,
            expiresAt,
          };
          const scope = sessionScopeFromBody(body);
          if (scope !== undefined) {
            Object.assign(relayRequest, { scope });
          }
          return json(
            await options.loginRelay.start(relayRequest)
          );
        }
        return json(
          createInternalLoginHandoff({
            connectionId,
            handoffToken,
            expiresAt,
          })
        );
      }

      if (request.method === "GET" && url.pathname === "/internal/session/login-relay/snapshot") {
        if (options.loginRelay === undefined) {
          return json({ error: { code: "login_relay_not_configured" } }, 501);
        }
        const connectionId = stringValue(url.searchParams.get("connectionId"), "connectionId");
        return json(await options.loginRelay.snapshot(connectionId));
      }

      if (request.method === "POST" && url.pathname === "/internal/session/login-relay/input") {
        if (options.loginRelay === undefined) {
          return json({ error: { code: "login_relay_not_configured" } }, 501);
        }
        return json(await options.loginRelay.input(loginRelayInputBody(await readJsonObject(request))));
      }

      if (request.method === "POST" && url.pathname === "/internal/session/login-relay/complete") {
        if (options.loginRelay === undefined) {
          return json({ error: { code: "login_relay_not_configured" } }, 501);
        }
        const body = await readJsonObject(request);
        return json(await options.loginRelay.complete(stringValue(body.connectionId, "connectionId")));
      }

      if (request.method === "POST" && url.pathname === "/internal/session/revoke") {
        const scope = await readSessionScope(request);
        if (options.sessionStore !== undefined) {
          await options.sessionStore.revoke(scope);
        }
        return json({ connectionId: scope.connectionId, status: "revoked" });
      }

      if (request.method === "POST" && url.pathname === "/internal/search/cards") {
        const body = await readJsonObject(request);
        const connectionId = stringValue(body.connectionId, "connectionId");
        const sessionStatus = await statusFor(options, connectionId, sessionScopeFromBody(body));
        if (sessionStatus.status !== "ready") {
          return json({ error: { code: "session_not_ready", status: sessionStatus.status } }, 409);
        }
        const cardSearchBody = cardSearchRequestBody(body);
        if (options.cardSearchHandler === undefined) {
          return json({ error: { code: "card_search_not_configured" } }, 501);
        }
        return json(await options.cardSearchHandler(cardSearchBody));
      }

      if (request.method === "POST" && url.pathname === "/internal/details/open") {
        const body = await readJsonObject(request);
        if (containsBudgetField(body)) {
          return json({ error: { code: "budget_decision_not_allowed_in_worker" } }, 400);
        }
        const detailOpenBody = detailOpenRequestBody(body);
        if (options.detailOpenKeyApproved === undefined) {
          return json({ error: { code: "detail_open_approval_not_configured" } }, 403);
        }
        for (const item of detailOpenBody.requests) {
          if (options.detailOpenKeyApproved(detailOpenBody, item) !== true) {
            return json({ error: { code: "unapproved_idempotency_key" } }, 403);
          }
        }
        if (options.detailOpenHandler === undefined) {
          return json({ error: { code: "detail_open_not_configured" } }, 501);
        }
        return json(await options.detailOpenHandler(detailOpenBody));
      }
    } catch (error) {
      if (error instanceof LoginRelayNotVerifiedError) {
        return json({ error: { code: "login_not_verified" } }, 409);
      }
      return json({ error: { code: "invalid_worker_request" } }, 400);
    }

    return json({ error: { code: "not_found" } }, 404);
  };
}

export function createWorkerFetchHandlerFromEnv(env: Record<string, string | undefined>): (request: Request) => Promise<Response> {
  const authToken = env.SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN;
  if (!authToken) {
    throw new Error("Missing SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN.");
  }
  const sessionStoreDir = env.liepin_session_store_dir ?? env.SEEKTALENT_LIEPIN_SESSION_STORE_DIR;
  if (!sessionStoreDir) {
    throw new Error("Missing Liepin session store directory environment.");
  }
  const sessionStore = new EncryptedSessionStore(sessionStoreDir, loadSessionStoreKeyFromEnv(env));
  const detailOpenApprovalSecret =
    env.liepin_detail_open_approval_secret ?? env.SEEKTALENT_LIEPIN_DETAIL_OPEN_APPROVAL_SECRET;
  const options: WorkerFetchOptions = {
    authToken,
    sessionStore,
    cardSearchHandler: createProductionCardSearchHandler(sessionStore),
    detailOpenHandler: createProductionDetailOpenHandler(sessionStore, {
      allowDataDetailUrls:
        env.NODE_ENV === "test" && env.SEEKTALENT_LIEPIN_WORKER_TEST_ALLOW_DATA_DETAIL_URLS === "1",
    }),
    loginRelay: new PlaywrightLoginRelayController(sessionStore),
  };
  if (detailOpenApprovalSecret) {
    options.detailOpenKeyApproved = (body, request) =>
      isApprovedDetailOpenRequest(body, request, detailOpenApprovalSecret);
  }
  return createWorkerFetchHandler(options);
}

function createProductionCardSearchHandler(
  sessionStore: EncryptedSessionStore,
): (body: CardSearchRequestBody) => Promise<object> {
  return async (body: CardSearchRequestBody): Promise<object> => {
    const storageState = await sessionStore.readStorageState(cardSearchSessionScope(body));
    const browser = await chromium.launch({ headless: true });
    const contextOptions: BrowserContextOptions = {};
    contextOptions.storageState = storageState as NonNullable<BrowserContextOptions["storageState"]>;
    const context = await browser.newContext(contextOptions);
    try {
      const page = await context.newPage();
      return await searchCards({ page, request: body });
    } finally {
      await context.close();
      await browser.close();
    }
  };
}

function cardSearchSessionScope(body: CardSearchRequestBody): SessionScope {
  return {
    tenantId: body.tenantId,
    workspaceId: body.workspaceId,
    providerAccountHash: body.providerAccountHash,
    connectionId: body.connectionId,
  };
}

function createProductionDetailOpenHandler(
  sessionStore: EncryptedSessionStore,
  options: { allowDataDetailUrls: boolean },
): (body: DetailOpenRequestBody) => Promise<object> {
  return async (body: DetailOpenRequestBody): Promise<object> => {
    const storageState = await sessionStore.readStorageState(detailOpenSessionScope(body));
    const browser = await chromium.launch({ headless: true });
    const contextOptions: BrowserContextOptions = {};
    contextOptions.storageState = storageState as NonNullable<BrowserContextOptions["storageState"]>;
    const context = await browser.newContext(contextOptions);
    try {
      const page = await context.newPage();
      return await openDetails({
        page,
        requests: body.requests,
        workerCommandId: body.workerCommandId,
        openRequest: async (detailRequest) => {
          await page.goto(detailUrlForRequest(detailRequest, options), { waitUntil: "domcontentloaded" });
        },
      });
    } finally {
      await context.close();
      await browser.close();
    }
  };
}

function detailOpenSessionScope(body: DetailOpenRequestBody): SessionScope {
  return {
    tenantId: stringValue(body.tenantId, "tenantId"),
    workspaceId: stringValue(body.workspaceId, "workspaceId"),
    providerAccountHash: stringValue(body.providerAccountHash, "providerAccountHash"),
    connectionId: stringValue(body.connectionId, "connectionId"),
  };
}

function cardSearchRequestBody(body: Record<string, unknown>): CardSearchRequestBody {
  const parsed: CardSearchRequestBody = {
    tenantId: stringValue(body.tenantId, "tenantId"),
    workspaceId: stringValue(body.workspaceId, "workspaceId"),
    providerAccountHash: stringValue(body.providerAccountHash, "providerAccountHash"),
    connectionId: stringValue(body.connectionId, "connectionId"),
    keyword: stringValue(body.keyword, "keyword"),
    pageSize: positiveIntegerValue(body.pageSize, "pageSize"),
    round: positiveIntegerValue(body.round, "round"),
    traceId: stringValue(body.traceId, "traceId"),
  };
  if (typeof body.cursor === "string" && body.cursor.trim()) {
    parsed.cursor = body.cursor.trim();
  }
  if (isObject(body.providerFilters)) {
    parsed.providerFilters = safeProviderFilters(body.providerFilters);
  }
  return parsed;
}

function isApprovedDetailOpenRequest(
  body: DetailOpenRequestBody,
  request: DetailOpenRequestBody["requests"][number],
  secret: string,
): boolean {
  if (!request.approvalKey || request.approvalKey.length > 1024 || /\s/.test(request.approvalKey)) {
    return false;
  }
  if (!request.idempotencyKey.startsWith("open:") || request.idempotencyKey.length > 260 || /\s/.test(request.idempotencyKey)) {
    return false;
  }
  const prefix = "detail-open:v1:";
  if (!request.approvalKey.startsWith(prefix)) {
    return false;
  }
  const token = request.approvalKey.slice(prefix.length);
  const parts = token.split(".");
  if (parts.length !== 2 || !parts[0] || !parts[1]) {
    return false;
  }
  const encodedPayload = parts[0];
  const signature = parts[1];
  const expectedSignature = createHmac("sha256", secret).update(encodedPayload).digest("base64url");
  if (!constantTimeEqual(signature, expectedSignature)) {
    return false;
  }
  try {
    const payload = JSON.parse(Buffer.from(encodedPayload, "base64url").toString("utf8")) as Record<string, unknown>;
    return (
      payload.v === 1 &&
      payload.tenantId === body.tenantId &&
      payload.workspaceId === body.workspaceId &&
      payload.providerAccountHash === body.providerAccountHash &&
      payload.connectionId === body.connectionId &&
      payload.providerDayKey === body.providerDayKey &&
      payload.candidateId === request.candidateId &&
      payload.idempotencyKey === request.idempotencyKey
    );
  } catch {
    return false;
  }
}

function constantTimeEqual(actual: string, expected: string): boolean {
  const actualBuffer = Buffer.from(actual);
  const expectedBuffer = Buffer.from(expected);
  if (actualBuffer.length !== expectedBuffer.length) {
    return false;
  }
  return timingSafeEqual(actualBuffer, expectedBuffer);
}

function detailUrlForRequest(
  request: { candidateId: string; detailUrl?: string },
  options: { allowDataDetailUrls: boolean },
): string {
  if (request.detailUrl) {
    return safeDetailUrl(request.detailUrl, options);
  }
  return `https://www.liepin.com/candidate/${encodeURIComponent(request.candidateId)}`;
}

function safeDetailUrl(rawUrl: string, options: { allowDataDetailUrls: boolean }): string {
  const parsed = new URL(rawUrl);
  if (options.allowDataDetailUrls && parsed.protocol === "data:") {
    return rawUrl;
  }
  if (parsed.protocol === "https:" && (parsed.hostname === "liepin.com" || parsed.hostname.endsWith(".liepin.com"))) {
    return rawUrl;
  }
  throw new Error("Liepin detail URL must use a Liepin HTTPS URL.");
}

function authorize(request: Request, authToken: string): Response | null {
  const header = request.headers.get("authorization");
  if (!header) {
    return json({ error: { code: "worker_auth_required" } }, 401);
  }
  if (header !== `Bearer ${authToken}`) {
    return json({ error: { code: "worker_auth_forbidden" } }, 403);
  }
  return null;
}

async function statusFor(
  options: WorkerFetchOptions,
  connectionId: string,
  scope?: SessionScope,
): Promise<SessionStatusResponse> {
  if (options.sessionStatus !== undefined) {
    return {
      ...DEFAULT_SESSION_STATUS,
      ...options.sessionStatus,
      connectionId,
    };
  }
  if (options.sessionStore !== undefined && scope !== undefined) {
    try {
      await options.sessionStore.readStorageState(scope);
      return {
        connectionId,
        status: "ready",
        providerAccountHash: scope.providerAccountHash,
        fixtureOnly: false,
      };
    } catch {
      return {
        connectionId,
        status: "missing",
        providerAccountHash: scope.providerAccountHash,
        fixtureOnly: false,
      };
    }
  }
  return {
    ...DEFAULT_SESSION_STATUS,
    connectionId,
  };
}

function sessionScopeFromQuery(url: URL): SessionScope | undefined {
  const tenantId = url.searchParams.get("tenantId");
  const workspaceId = url.searchParams.get("workspaceId");
  const providerAccountHash = url.searchParams.get("providerAccountHash");
  const connectionId = url.searchParams.get("connectionId");
  if (!tenantId || !workspaceId || !providerAccountHash || !connectionId) {
    return undefined;
  }
  return { tenantId, workspaceId, providerAccountHash, connectionId };
}

function sessionScopeFromBody(body: Record<string, unknown>): SessionScope | undefined {
  if (
    typeof body.tenantId !== "string" ||
    typeof body.workspaceId !== "string" ||
    typeof body.providerAccountHash !== "string" ||
    typeof body.connectionId !== "string"
  ) {
    return undefined;
  }
  return {
    tenantId: body.tenantId,
    workspaceId: body.workspaceId,
    providerAccountHash: body.providerAccountHash,
    connectionId: body.connectionId,
  };
}

async function readJsonObject(request: Request): Promise<Record<string, unknown>> {
  const body = await request.json();
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    throw new Error("Expected JSON object body.");
  }
  return body as Record<string, unknown>;
}

async function readSessionScope(request: Request): Promise<SessionScope> {
  const body = await readJsonObject(request);
  return {
    tenantId: stringValue(body.tenantId, "tenantId"),
    workspaceId: stringValue(body.workspaceId, "workspaceId"),
    providerAccountHash: stringValue(body.providerAccountHash, "providerAccountHash"),
    connectionId: stringValue(body.connectionId, "connectionId"),
  };
}

function stringValue(value: unknown, fieldName: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Missing ${fieldName}.`);
  }
  return value;
}

function positiveIntegerValue(value: unknown, fieldName: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0) {
    throw new Error(`Missing ${fieldName}.`);
  }
  return value;
}

function safeProviderFilters(filters: Record<string, unknown>): Record<string, unknown> {
  const safe: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(filters)) {
    if (!key || key.startsWith("liepin_")) {
      continue;
    }
    if (typeof value === "string" || typeof value === "number") {
      safe[key] = value;
      continue;
    }
    if (Array.isArray(value)) {
      const safeItems = value.filter((item): item is string => typeof item === "string");
      if (safeItems.length > 0) {
        safe[key] = safeItems;
      }
    }
  }
  return safe;
}

function loginRelayInputBody(body: Record<string, unknown>): LoginRelayInputRequest {
  const action = stringValue(body.action, "action");
  if (action !== "click" && action !== "type" && action !== "key") {
    throw new Error("Unsupported login relay action.");
  }
  const parsed: LoginRelayInputRequest = {
    connectionId: stringValue(body.connectionId, "connectionId"),
    action,
  };
  if (typeof body.x === "number") {
    parsed.x = body.x;
  }
  if (typeof body.y === "number") {
    parsed.y = body.y;
  }
  if (typeof body.text === "string") {
    parsed.text = body.text;
  }
  if (typeof body.key === "string") {
    parsed.key = body.key;
  }
  return parsed;
}

function containsBudgetField(body: Record<string, unknown>): boolean {
  return Object.entries(body).some(([key, value]) => {
    if (key.toLowerCase().includes("budget")) {
      return true;
    }
    if (Array.isArray(value)) {
      return value.some((entry) => isObject(entry) && containsBudgetField(entry));
    }
    return isObject(value) && containsBudgetField(value);
  });
}

function detailOpenRequestBody(body: Record<string, unknown>): DetailOpenRequestBody {
  const workerCommandId = stringValue(body.workerCommandId, "workerCommandId");
  if (!Array.isArray(body.requests) || body.requests.length === 0) {
    throw new Error("Missing requests.");
  }
  const parsed: DetailOpenRequestBody = {
    workerCommandId,
    requests: body.requests.map((entry) => {
      if (!isObject(entry)) {
        throw new Error("Invalid detail request.");
      }
      const requestItem = {
        requestId: stringValue(entry.requestId, "requestId"),
        attemptId: stringValue(entry.attemptId, "attemptId"),
        idempotencyKey: stringValue(entry.idempotencyKey, "idempotencyKey"),
        candidateId: stringValue(entry.candidateId, "candidateId"),
      };
      const parsedItem = { ...requestItem };
      if (typeof entry.approvalKey === "string" && entry.approvalKey.trim()) {
        Object.assign(parsedItem, { approvalKey: entry.approvalKey.trim() });
      }
      if (typeof entry.detailUrl === "string" && entry.detailUrl.trim()) {
        return { ...parsedItem, detailUrl: entry.detailUrl.trim() };
      }
      return parsedItem;
    }),
  };
  if (typeof body.tenantId === "string") {
    parsed.tenantId = body.tenantId;
  }
  if (typeof body.workspaceId === "string") {
    parsed.workspaceId = body.workspaceId;
  }
  if (typeof body.providerAccountHash === "string") {
    parsed.providerAccountHash = body.providerAccountHash;
  }
  if (typeof body.connectionId === "string") {
    parsed.connectionId = body.connectionId;
  }
  if (typeof body.providerDayKey === "string") {
    parsed.providerDayKey = body.providerDayKey;
  }
  return parsed;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function json(payload: object, status = 200): Response {
  return Response.json(payload, { status });
}

if (import.meta.main) {
  const host = validateServerHost(argValue("--host") ?? process.env.SEEKTALENT_LIEPIN_WORKER_HOST ?? "127.0.0.1");
  const port = Number(argValue("--port") ?? process.env.SEEKTALENT_LIEPIN_WORKER_PORT ?? "8123");
  Bun.serve({
    hostname: host,
    port,
    fetch: createWorkerFetchHandlerFromEnv(process.env),
  });
}

export function validateServerHost(host: string): string {
  const trimmed = host.trim();
  if (trimmed === "localhost" || trimmed === "::1") {
    return trimmed;
  }
  if (isIP(trimmed) === 4 && trimmed.startsWith("127.")) {
    return trimmed;
  }
  throw new Error("Liepin worker server host must be loopback.");
}

function argValue(name: string): string | undefined {
  const index = process.argv.indexOf(name);
  if (index === -1) {
    return undefined;
  }
  return process.argv[index + 1];
}
