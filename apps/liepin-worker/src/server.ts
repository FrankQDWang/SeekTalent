import { randomUUID } from "node:crypto";
import { isIP } from "node:net";

import { WORKER_CONTRACT_VERSION } from "./contracts";
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
  detailOpenKeyApproved?: (idempotencyKey: string) => boolean;
  detailOpenHandler?: (body: DetailOpenRequestBody) => Promise<object>;
  handoffTokenFactory?: () => string;
  now?: () => Date;
};

type DetailOpenRequestBody = {
  workerCommandId: string;
  requests: Array<{
    requestId: string;
    attemptId: string;
    idempotencyKey: string;
    candidateId: string;
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
        return json(
          createInternalLoginHandoff({
            connectionId,
            handoffToken: options.handoffTokenFactory?.() ?? randomUUID(),
            expiresAt,
          })
        );
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
        return json({ error: { code: "search_not_implemented" } }, 501);
      }

      if (request.method === "POST" && url.pathname === "/internal/details/open") {
        const body = await readJsonObject(request);
        if (containsBudgetField(body)) {
          return json({ error: { code: "budget_decision_not_allowed_in_worker" } }, 400);
        }
        const detailOpenBody = detailOpenRequestBody(body);
        for (const item of detailOpenBody.requests) {
          if (options.detailOpenKeyApproved !== undefined && options.detailOpenKeyApproved(item.idempotencyKey) !== true) {
            return json({ error: { code: "unapproved_idempotency_key" } }, 403);
          }
        }
        if (options.detailOpenHandler === undefined) {
          return json({ error: { code: "detail_open_not_configured" } }, 501);
        }
        return json(await options.detailOpenHandler(detailOpenBody));
      }
    } catch {
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
  return createWorkerFetchHandler({
    authToken,
    sessionStore: new EncryptedSessionStore(sessionStoreDir, loadSessionStoreKeyFromEnv(env)),
  });
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
  return {
    workerCommandId,
    requests: body.requests.map((entry) => {
      if (!isObject(entry)) {
        throw new Error("Invalid detail request.");
      }
      return {
        requestId: stringValue(entry.requestId, "requestId"),
        attemptId: stringValue(entry.attemptId, "attemptId"),
        idempotencyKey: stringValue(entry.idempotencyKey, "idempotencyKey"),
        candidateId: stringValue(entry.candidateId, "candidateId"),
      };
    }),
  };
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
