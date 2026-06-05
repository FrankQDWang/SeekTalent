import { createHmac } from "node:crypto";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "bun:test";

import {
  createManagedBrowserPool,
  createWorkerFetchHandler,
  createWorkerFetchHandlerFromEnv,
  validateServerHost,
} from "../src/server";
import { LoginRelayNotVerifiedError, hasLiepinAuthenticatedState } from "../src/loginRelay";
import { EncryptedSessionStore, type SessionScope } from "../src/sessionStore";

const AUTH_TOKEN = "unit-worker-token";
const AUTH_HEADERS = { Authorization: `Bearer ${AUTH_TOKEN}` };
const DETAIL_APPROVAL_SECRET = "unit-detail-approval-secret";
const PROVIDER_DAY_KEY = "liepin:acct-hash:2026-05-07";
const SCOPE: SessionScope = {
  tenantId: "tenant-a",
  workspaceId: "workspace-a",
  providerAccountHash: "acct-hash",
  connectionId: "conn-1",
};

function detailApprovalKey(input: {
  tenantId: string;
  workspaceId: string;
  providerAccountHash: string;
  connectionId: string;
  providerDayKey: string;
  candidateId: string;
  idempotencyKey: string;
  detailUrl?: string;
}): string {
  const payload = {
    v: 1,
    tenantId: input.tenantId,
    workspaceId: input.workspaceId,
    providerAccountHash: input.providerAccountHash,
    connectionId: input.connectionId,
    providerDayKey: input.providerDayKey,
    candidateId: input.candidateId,
    idempotencyKey: input.idempotencyKey,
    ...(input.detailUrl ? { detailUrl: input.detailUrl } : {}),
  };
  const encodedPayload = Buffer.from(JSON.stringify(sortObjectKeys(payload)), "utf8").toString("base64url");
  const signature = createHmac("sha256", DETAIL_APPROVAL_SECRET).update(encodedPayload).digest("base64url");
  return `detail-open:v1:${encodedPayload}.${signature}`;
}

function sortObjectKeys(payload: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(payload).sort(([left], [right]) => left.localeCompare(right)));
}

describe("internal Liepin worker server", () => {
  it("allows only loopback hosts for the direct Bun entrypoint", () => {
    expect(validateServerHost("localhost")).toBe("localhost");
    expect(validateServerHost("127.0.0.1")).toBe("127.0.0.1");
    expect(validateServerHost("::1")).toBe("::1");

    expect(() => validateServerHost("0.0.0.0")).toThrow("loopback");
    expect(() => validateServerHost("192.168.1.20")).toThrow("loopback");
  });

  it("returns minimal health readiness without browser or session internals", async () => {
    const handler = createWorkerFetchHandler({ authToken: AUTH_TOKEN });

    const response = await handler(new Request("http://127.0.0.1/internal/health", { headers: AUTH_HEADERS }));

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload).toEqual({ status: "ok", workerVersion: "liepin-worker-v1" });
    expectLowercaseJson(JSON.stringify(payload).toLowerCase()).not.toContainAny([
      "cdp",
      "debug",
      "browser",
      "storage",
      "sessionpath",
      "base_url",
    ]);
  });

  it("returns session status as domain status only", async () => {
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      sessionStatus: {
        connectionId: "conn-1",
        status: "ready",
        providerAccountHash: "acct-hash",
        fixtureOnly: false,
      },
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/session/status?connectionId=conn-1", { headers: AUTH_HEADERS })
    );

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload).toEqual({
      connectionId: "conn-1",
      status: "ready",
      providerAccountHash: "acct-hash",
      fixtureOnly: false,
    });
    expectLowercaseJson(JSON.stringify(payload)).not.toContainAny(["cdp", "debug", "storage", "browser"]);
  });

  it("returns login handoff token without CDP debug or storage fields", async () => {
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      handoffTokenFactory: () => "handoff-token",
      now: () => new Date("2026-05-08T12:00:00Z"),
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/session/login-handoff", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ connectionId: "conn-1" }),
      })
    );

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload).toEqual({
      connectionId: "conn-1",
      handoffToken: "handoff-token",
      loginUrl: "https://www.liepin.com/",
      expiresAt: "2026-05-08T12:05:00Z",
    });
    expectLowercaseJson(JSON.stringify(payload)).not.toContainAny([
      "cdp",
      "debug",
      "storage",
      "worker",
      "base_url",
    ]);
  });

  it("relays login through safe snapshots and input events without browser internals", async () => {
    const calls: string[] = [];
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      handoffTokenFactory: () => "handoff-token",
      now: () => new Date("2026-05-08T12:00:00Z"),
      loginRelay: {
        async start(request) {
          calls.push(`start:${request.connectionId}:${request.scope?.workspaceId ?? "none"}`);
          return {
            connectionId: request.connectionId,
            handoffToken: request.handoffToken,
            loginUrl: "https://www.liepin.com/",
            expiresAt: request.expiresAt.toISOString().replace(".000Z", "Z"),
          };
        },
        async snapshot(connectionId) {
          calls.push(`snapshot:${connectionId}`);
          return {
            connectionId,
            status: "login_in_progress",
            pageTitle: "Liepin",
            pageOrigin: "https://www.liepin.com",
            imageMimeType: "image/jpeg",
            imageBase64: "safe-image",
            updatedAt: "2026-05-08T12:00:01Z",
          };
        },
        async input(request) {
          calls.push(`input:${request.connectionId}:${request.action}`);
          return { connectionId: request.connectionId, accepted: true, updatedAt: "2026-05-08T12:00:02Z" };
        },
        async complete(connectionId) {
          calls.push(`complete:${connectionId}`);
          return { connectionId, status: "ready", providerAccountHash: "acct-hash", fixtureOnly: false };
        },
      },
    });

    const handoff = await handler(
      new Request("http://127.0.0.1/internal/session/login-handoff", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ ...SCOPE, connectionId: "conn-1" }),
      })
    );
    const snapshot = await handler(
      new Request("http://127.0.0.1/internal/session/login-relay/snapshot?connectionId=conn-1", {
        headers: AUTH_HEADERS,
      })
    );
    const input = await handler(
      new Request("http://127.0.0.1/internal/session/login-relay/input", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ connectionId: "conn-1", action: "click", x: 20, y: 30 }),
      })
    );
    const complete = await handler(
      new Request("http://127.0.0.1/internal/session/login-relay/complete", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ connectionId: "conn-1" }),
      })
    );

    expect(handoff.status).toBe(200);
    expect(snapshot.status).toBe(200);
    expect(input.status).toBe(200);
    expect(complete.status).toBe(200);
    expect(await complete.json()).toEqual({
      connectionId: "conn-1",
      status: "ready",
      providerAccountHash: "acct-hash",
      fixtureOnly: false,
    });
    expect(calls).toEqual([
      "start:conn-1:workspace-a",
      "snapshot:conn-1",
      "input:conn-1:click",
      "complete:conn-1",
    ]);
    expectLowercaseJson(
      JSON.stringify({
        handoff: await handoff.json(),
        snapshot: await snapshot.json(),
        input: await input.json(),
      }).toLowerCase()
    ).not.toContainAny(["cdp", "debug", "storagestate", "storage_state", "websocket", "base_url"]);
  });

  it("rejects login relay completion when the worker cannot verify Liepin login", async () => {
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      loginRelay: {
        async start(request) {
          return {
            connectionId: request.connectionId,
            handoffToken: request.handoffToken,
            loginUrl: "https://www.liepin.com/",
            expiresAt: request.expiresAt.toISOString().replace(".000Z", "Z"),
          };
        },
        async snapshot(connectionId) {
          return {
            connectionId,
            status: "login_in_progress",
            pageTitle: "Liepin",
            pageOrigin: "https://www.liepin.com",
            imageMimeType: "image/jpeg",
            imageBase64: "safe-image",
            updatedAt: "2026-05-08T12:00:01Z",
          };
        },
        async input(request) {
          return { connectionId: request.connectionId, accepted: true, updatedAt: "2026-05-08T12:00:02Z" };
        },
        async complete() {
          throw new LoginRelayNotVerifiedError();
        },
      },
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/session/login-relay/complete", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ connectionId: "conn-1" }),
      })
    );

    expect(response.status).toBe(409);
    const payload = await response.json();
    expect(payload).toEqual({ error: { code: "login_not_verified" } });
    expectLowercaseJson(JSON.stringify(payload).toLowerCase()).not.toContainAny(["cookie", "storage", "cdp", "debug"]);
  });

  it("requires a Liepin-domain authenticated cookie before storing login relay state", () => {
    expect(hasLiepinAuthenticatedState({ cookies: [], origins: [] })).toBe(false);
    expect(
      hasLiepinAuthenticatedState({
        cookies: [{ name: "__gc_id", value: "analytics", domain: ".liepin.com", path: "/" }],
        origins: [],
      })
    ).toBe(false);
    expect(
      hasLiepinAuthenticatedState({
        cookies: [{ name: "lt_auth", value: "secret", domain: ".example.com", path: "/" }],
        origins: [],
      })
    ).toBe(false);
    expect(
      hasLiepinAuthenticatedState({
        cookies: [{ name: "lt_auth", value: "secret", domain: ".liepin.com", path: "/" }],
        origins: [],
      })
    ).toBe(true);
  });

  it("revokes encrypted session state and returns domain status only", async () => {
    const rootDir = await mkdtemp(join(tmpdir(), "liepin-worker-server-"));
    const store = new EncryptedSessionStore(rootDir, {
      keyId: "unit-key",
      keyMaterial: "unit-test-key-material",
    });
    await store.writeStorageState(SCOPE, { cookies: [{ name: "lt", value: "secret" }], origins: [] });
    const handler = createWorkerFetchHandlerFromEnv({
      SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN: AUTH_TOKEN,
      SEEKTALENT_LIEPIN_SESSION_STORE_DIR: rootDir,
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID: "env-key",
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY: "env-test-key-material",
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/session/revoke", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify(SCOPE),
      })
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ connectionId: "conn-1", status: "revoked" });
    await expect(store.readStorageState(SCOPE)).rejects.toThrow("not found");
  });

  it("builds the production handler from env and revokes encrypted session state", async () => {
    const rootDir = await mkdtemp(join(tmpdir(), "liepin-worker-env-server-"));
    const store = new EncryptedSessionStore(rootDir, {
      keyId: "env-key",
      keyMaterial: "env-test-key-material",
    });
    await store.writeStorageState(SCOPE, { cookies: [{ name: "lt", value: "secret" }], origins: [] });
    const handler = createWorkerFetchHandlerFromEnv({
      SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN: AUTH_TOKEN,
      SEEKTALENT_LIEPIN_SESSION_STORE_DIR: rootDir,
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID: "env-key",
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY: "env-test-key-material",
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/session/revoke", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify(SCOPE),
      })
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ connectionId: "conn-1", status: "revoked" });
    await expect(store.readStorageState(SCOPE)).rejects.toThrow("not found");
  });

  it("builds the production handler from env and reports encrypted session status by scope", async () => {
    const rootDir = await mkdtemp(join(tmpdir(), "liepin-worker-env-status-"));
    const store = new EncryptedSessionStore(rootDir, {
      keyId: "env-key",
      keyMaterial: "env-test-key-material",
    });
    await store.writeStorageState(SCOPE, { cookies: [{ name: "lt", value: "secret" }], origins: [] });
    const handler = createWorkerFetchHandlerFromEnv({
      SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN: AUTH_TOKEN,
      SEEKTALENT_LIEPIN_SESSION_STORE_DIR: rootDir,
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID: "env-key",
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY: "env-test-key-material",
    });
    const query = new URLSearchParams({
      tenantId: SCOPE.tenantId,
      workspaceId: SCOPE.workspaceId,
      providerAccountHash: SCOPE.providerAccountHash,
      connectionId: SCOPE.connectionId,
    });

    const ready = await handler(
      new Request(`http://127.0.0.1/internal/session/status?${query.toString()}`, { headers: AUTH_HEADERS })
    );
    const missing = await handler(
      new Request(
        `http://127.0.0.1/internal/session/status?${query.toString().replace("conn-1", "missing-conn")}`,
        { headers: AUTH_HEADERS }
      )
    );

    expect(ready.status).toBe(200);
    const readyPayload = await ready.json();
    expect(readyPayload).toEqual({
      connectionId: "conn-1",
      status: "ready",
      fixtureOnly: false,
    });
    expectLowercaseJson(JSON.stringify(readyPayload).toLowerCase()).not.toContainAny([
      "path",
      "storage",
      "cookie",
      "secret",
      "env-test-key-material",
    ]);

    expect(missing.status).toBe(200);
    expect(await missing.json()).toEqual({
      connectionId: "missing-conn",
      status: "missing",
      fixtureOnly: false,
    });
  });

  it("reuses a managed browser across production card and detail requests while isolating contexts", async () => {
    const rootDir = await mkdtemp(join(tmpdir(), "liepin-worker-managed-browser-"));
    const store = new EncryptedSessionStore(rootDir, {
      keyId: "env-key",
      keyMaterial: "env-test-key-material",
    });
    const secondScope = { ...SCOPE, connectionId: "conn-2", providerAccountHash: "acct-hash-2" };
    await store.writeStorageState(SCOPE, { cookies: [{ name: "lt", value: "session-a" }], origins: [] });
    await store.writeStorageState(secondScope, { cookies: [{ name: "lt", value: "session-b" }], origins: [] });
    const launcher = new FakeBrowserLauncher(cardFixtureHtml("card-1"));
    const browserPool = createManagedBrowserPool({ launcher });
    const handler = createWorkerFetchHandlerFromEnv(
      {
        NODE_ENV: "test",
        SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN: AUTH_TOKEN,
        SEEKTALENT_LIEPIN_SESSION_STORE_DIR: rootDir,
        SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID: "env-key",
        SEEKTALENT_LIEPIN_SESSION_STORE_KEY: "env-test-key-material",
        SEEKTALENT_LIEPIN_DETAIL_OPEN_APPROVAL_SECRET: DETAIL_APPROVAL_SECRET,
        SEEKTALENT_LIEPIN_WORKER_TEST_ALLOW_DATA_DETAIL_URLS: "1",
      },
      { browserPool }
    );

    const firstCard = await handler(
      new Request("http://127.0.0.1/internal/search/cards", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ ...SCOPE, keyword: "python", pageSize: 10, round: 1, traceId: "trace-1" }),
      })
    );
    const secondCard = await handler(
      new Request("http://127.0.0.1/internal/search/cards", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ ...secondScope, keyword: "spark", pageSize: 10, round: 2, traceId: "trace-2" }),
      })
    );
    const detailUrl = `data:text/html;charset=utf-8,${encodeURIComponent(detailFixtureHtml("detail-1"))}`;
    const detail = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          ...SCOPE,
          providerDayKey: PROVIDER_DAY_KEY,
          workerCommandId: "cmd-managed",
          requests: [
            {
              requestId: "request-managed",
              attemptId: "attempt-managed",
              idempotencyKey: "open:detail-1",
              approvalKey: detailApprovalKey({
                ...SCOPE,
                providerDayKey: PROVIDER_DAY_KEY,
                candidateId: "detail-1",
                idempotencyKey: "open:detail-1",
                detailUrl,
              }),
              candidateId: "detail-1",
              detailUrl,
            },
          ],
        }),
      })
    );

    expect(firstCard.status).toBe(200);
    expect(secondCard.status).toBe(200);
    expect(detail.status).toBe(200);
    expect(launcher.launchCalls).toBe(1);
    expect(launcher.contextStorageCookies()).toEqual(["session-a", "session-b", "session-a"]);
    expect(launcher.contextCloseCalls).toBe(3);
    expect(launcher.browserCloseCalls).toBe(0);

    await browserPool.shutdown();

    expect(launcher.browserCloseCalls).toBe(1);
  });

  it("cleans up the managed browser after the idle TTL", async () => {
    let now = 1_000;
    const launcher = new FakeBrowserLauncher(cardFixtureHtml("card-ttl"));
    const browserPool = createManagedBrowserPool({ launcher, idleTtlMs: 50, now: () => now });

    await browserPool.withPage({
      storageState: { cookies: [], origins: [] },
      run: async (page) => {
        await page.goto("https://www.liepin.com/");
        return {};
      },
    });
    expect(launcher.launchCalls).toBe(1);
    expect(launcher.browserCloseCalls).toBe(0);

    now += 51;
    await browserPool.cleanupIdle();

    expect(launcher.browserCloseCalls).toBe(1);
  });

  it("rebuilds the managed browser after a fatal context error", async () => {
    const launcher = new FakeBrowserLauncher(cardFixtureHtml("card-rebuild"));
    launcher.failNextNewContext = true;
    const browserPool = createManagedBrowserPool({ launcher });

    await browserPool.withPage({
      storageState: { cookies: [], origins: [] },
      run: async (page) => {
        await page.goto("https://www.liepin.com/");
        return {};
      },
    });

    expect(launcher.launchCalls).toBe(2);
    expect(launcher.browserCloseCalls).toBe(1);
    expect(launcher.contextCloseCalls).toBe(1);
  });

  it("fails closed when production handler env is missing session key material", () => {
    expect(() =>
      createWorkerFetchHandlerFromEnv({
        SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN: AUTH_TOKEN,
        SEEKTALENT_LIEPIN_SESSION_STORE_DIR: "/tmp/liepin-sessions",
        SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID: "env-key",
      })
    ).toThrow("Missing Liepin session store key environment.");
  });

  it("refuses card search when the session is not ready", async () => {
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      sessionStatus: { connectionId: "conn-1", status: "login_required", fixtureOnly: false },
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/search/cards", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ connectionId: "conn-1", keywordQuery: "python" }),
      })
    );

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({
      error: { code: "session_not_ready", status: "login_required" },
    });
  });

  it("runs an injected card search handler after the session is ready", async () => {
    let seenBody: unknown;
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      sessionStatus: {
        connectionId: "conn-1",
        status: "ready",
        providerAccountHash: "acct-hash",
        fixtureOnly: false,
      },
      cardSearchHandler: async (body) => {
        seenBody = body;
        return {
          cards: [
            {
              payload: { id: "candidate-1", title: "Python Engineer" },
              normalized_text: "Python Engineer",
              provider_subject_id: "candidate-1",
              provider_listing_id: "listing-1",
              synthetic_candidate_fingerprint: "liepin:candidate-1",
              identity_confidence: "provider_subject_id",
              extraction_source: "network",
              extractor_version: "liepin-passive-extractor-v1",
              pii_classification: "no_direct_contact",
              retention_policy: "provider_snapshot_7d",
              access_scope: "local_run_only",
              redaction_state: "raw_provider_payload",
            },
          ],
          diagnostics: ["network"],
          exhausted: false,
          nextCursor: "cursor-2",
          rawCandidateCount: 1,
          requestPayload: { keyword: "python", pageSize: 10, cursor: "cursor-1", round: 3, traceId: "trace-3" },
        };
      },
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/search/cards", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          ...SCOPE,
          keyword: "python",
          pageSize: 10,
          cursor: "cursor-1",
          round: 3,
          traceId: "trace-3",
          providerFilters: { city: "上海" },
        }),
      })
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      cards: [
        {
          normalized_text: "Python Engineer",
          provider_subject_id: "candidate-1",
          synthetic_candidate_fingerprint: "liepin:candidate-1",
        },
      ],
      rawCandidateCount: 1,
    });
    expect(seenBody).toEqual({
      ...SCOPE,
      keyword: "python",
      pageSize: 10,
      cursor: "cursor-1",
      round: 3,
      traceId: "trace-3",
      providerFilters: { city: "上海" },
    });
  });

  it("checks stored session readiness for card search using the full request scope", async () => {
    const rootDir = await mkdtemp(join(tmpdir(), "liepin-worker-search-status-"));
    const store = new EncryptedSessionStore(rootDir, {
      keyId: "env-key",
      keyMaterial: "env-test-key-material",
    });
    await store.writeStorageState(SCOPE, { cookies: [{ name: "lt", value: "secret" }], origins: [] });
    const handler = createWorkerFetchHandler({ authToken: AUTH_TOKEN, sessionStore: store });

    const ready = await handler(
      new Request("http://127.0.0.1/internal/search/cards", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ ...SCOPE, keyword: "python", pageSize: 10, round: 3, traceId: "trace-3" }),
      })
    );
    const missing = await handler(
      new Request("http://127.0.0.1/internal/search/cards", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          ...SCOPE,
          connectionId: "missing-conn",
          keyword: "python",
          pageSize: 10,
          round: 3,
          traceId: "trace-3",
        }),
      })
    );

    expect(ready.status).toBe(501);
    const readyPayload = await ready.json();
    expect(readyPayload).toEqual({ error: { code: "card_search_not_configured" } });
    expect(missing.status).toBe(409);
    expect(await missing.json()).toEqual({ error: { code: "session_not_ready", status: "missing" } });
  });

  it("builds the production handler from env and opens approved detail pages", async () => {
    const rootDir = await mkdtemp(join(tmpdir(), "liepin-worker-env-detail-"));
    const store = new EncryptedSessionStore(rootDir, {
      keyId: "env-key",
      keyMaterial: "env-test-key-material",
    });
    await store.writeStorageState(SCOPE, { cookies: [], origins: [] });
    const handler = createWorkerFetchHandlerFromEnv({
      NODE_ENV: "test",
      SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN: AUTH_TOKEN,
      SEEKTALENT_LIEPIN_SESSION_STORE_DIR: rootDir,
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID: "env-key",
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY: "env-test-key-material",
      SEEKTALENT_LIEPIN_DETAIL_OPEN_APPROVAL_SECRET: DETAIL_APPROVAL_SECRET,
      SEEKTALENT_LIEPIN_WORKER_TEST_ALLOW_DATA_DETAIL_URLS: "1",
    });
    const detailUrl = `data:text/html;charset=utf-8,${encodeURIComponent(`
      <article class="candidate-detail" data-candidate-id="env-candidate-1" data-detail-id="env-detail-1">
        <h1 class="candidate-title">Backend Engineer</h1>
        <div class="candidate-company">Redacted Cloud</div>
        <section class="candidate-summary">Python systems</section>
      </article>
    `)}`;

    const unsigned = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          ...SCOPE,
          providerDayKey: PROVIDER_DAY_KEY,
          workerCommandId: "cmd-env",
          requests: [
            {
              requestId: "request-env",
              attemptId: "attempt-env",
              idempotencyKey: "open:env-candidate-1",
              candidateId: "env-candidate-1",
              detailUrl,
            },
          ],
        }),
      })
    );
    expect(unsigned.status).toBe(403);
    expect(await unsigned.json()).toEqual({ error: { code: "unapproved_idempotency_key" } });

    const response = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          ...SCOPE,
          providerDayKey: PROVIDER_DAY_KEY,
          workerCommandId: "cmd-env",
          requests: [
            {
              requestId: "request-env",
              attemptId: "attempt-env",
              idempotencyKey: "open:env-candidate-1",
              approvalKey: detailApprovalKey({
                ...SCOPE,
                providerDayKey: PROVIDER_DAY_KEY,
                candidateId: "env-candidate-1",
                idempotencyKey: "open:env-candidate-1",
                detailUrl,
              }),
              candidateId: "env-candidate-1",
              detailUrl,
            },
          ],
        }),
      })
    );

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload).toMatchObject({
      workerCommandId: "cmd-env",
      results: [
        {
          requestId: "request-env",
          attemptId: "attempt-env",
          idempotencyKey: "open:env-candidate-1",
          status: "completed",
          diagnostics: { pageLoaded: true, payloadSeen: true, extractionSource: "dom_fallback" },
          candidate: {
            provider_subject_id: "env-candidate-1",
            normalized_text: expect.stringContaining("Python systems"),
          },
        },
      ],
    });
  });

  it("opens details from Python-approved body requests and returns the full response shape", async () => {
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      detailOpenKeyApproved: (_body, request) => request.idempotencyKey === "open:candidate-1",
      detailOpenHandler: async (body) => {
        const detailRequest = body.requests[0]!;
        return {
          workerCommandId: String(body.workerCommandId),
          results: [
            {
              requestId: detailRequest.requestId,
              attemptId: detailRequest.attemptId,
              idempotencyKey: detailRequest.idempotencyKey,
            status: "completed",
            workerResponseId: "worker-response-1",
            workerCommandId: String(body.workerCommandId),
            rawEvidenceRef: "worker://details/candidate-1.json",
            diagnostics: {
              pageLoaded: true,
              payloadSeen: true,
              extractionSource: "network",
              messages: [],
            },
            candidate: {
              payload: { candidateId: "candidate-1", title: "Backend Engineer" },
              normalized_text: "Backend Engineer Python",
              provider_subject_id: "candidate-1",
              provider_listing_id: null,
              synthetic_candidate_fingerprint: "candidate-1",
              identity_confidence: "provider_subject_id",
              extraction_source: "network",
              extractor_version: "liepin-passive-extractor-v1",
              pii_classification: "direct_contact_possible",
              retention_policy: "provider_snapshot_7d",
              access_scope: "local_run_only",
              redaction_state: "raw_provider_payload",
            },
            },
          ],
        };
      },
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          workerCommandId: "cmd-1",
          requests: [
            {
              requestId: "request-1",
              attemptId: "attempt-1",
              idempotencyKey: "open:candidate-1",
              candidateId: "candidate-1",
            },
          ],
        }),
      })
    );

    expect(response.status).toBe(200);
    const payload = await response.json();
    expect(payload).toMatchObject({
      workerCommandId: "cmd-1",
      results: [
        {
          requestId: "request-1",
          attemptId: "attempt-1",
          idempotencyKey: "open:candidate-1",
          status: "completed",
          candidate: {
            payload: { candidateId: "candidate-1" },
            normalized_text: "Backend Engineer Python",
          },
        },
      ],
    });
    expectLowercaseJson(JSON.stringify(payload).toLowerCase()).not.toContainAny([
      "storage",
      "cookie",
      "authorization",
      "cdp",
    ]);
  });

  it("does not open details when no approval verifier is configured", async () => {
    let handlerCalled = false;
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      detailOpenHandler: async (body) => {
        void body;
        handlerCalled = true;
        return { workerCommandId: "cmd-1", results: [] };
      },
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          workerCommandId: "cmd-1",
          requests: [{ requestId: "r1", attemptId: "a1", idempotencyKey: "open:candidate-1", candidateId: "c1" }],
        }),
      })
    );

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: { code: "detail_open_approval_not_configured" } });
    expect(handlerCalled).toBe(false);
  });

  it("rejects detail body budget fields and unapproved body idempotency keys", async () => {
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      sessionStatus: { connectionId: "conn-1", status: "ready", fixtureOnly: false },
      detailOpenKeyApproved: (_body, request) => request.idempotencyKey === "detail-approved",
      detailOpenHandler: async (body) => {
        handlerCalls += 1;
        return { workerCommandId: String(body.workerCommandId), results: [] };
      },
    });
    let handlerCalls = 0;

    const unapprovedKey = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          workerCommandId: "cmd-1",
          requests: [{ requestId: "r1", attemptId: "a1", idempotencyKey: "arbitrary-key", candidateId: "c1" }],
        }),
      })
    );
    expect(unapprovedKey.status).toBe(403);
    expect(await unapprovedKey.json()).toEqual({ error: { code: "unapproved_idempotency_key" } });
    expect(handlerCalls).toBe(0);

    const approvedKey = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          workerCommandId: "cmd-1",
          requests: [{ requestId: "r1", attemptId: "a1", idempotencyKey: "detail-approved", candidateId: "c1" }],
        }),
      })
    );
    expect(approvedKey.status).toBe(200);

    const withBudget = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          workerCommandId: "cmd-1",
          requests: [{ requestId: "r1", attemptId: "a1", idempotencyKey: "detail-approved", candidateId: "c1" }],
          budgetRemaining: 10,
        }),
      })
    );
    expect(withBudget.status).toBe(400);
    expect(await withBudget.json()).toEqual({ error: { code: "budget_decision_not_allowed_in_worker" } });
  });

  it("fails closed for detail open when no browser opener is configured", async () => {
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      detailOpenKeyApproved: (_body, request) => request.idempotencyKey === "detail-approved",
    });

    const response = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          workerCommandId: "cmd-1",
          requests: [{ requestId: "r1", attemptId: "a1", idempotencyKey: "detail-approved", candidateId: "c1" }],
        }),
      })
    );

    expect(response.status).toBe(501);
    expect(await response.json()).toEqual({ error: { code: "detail_open_not_configured" } });
  });

  it("rejects internal requests missing the Python worker auth token", async () => {
    const handler = createWorkerFetchHandler({ authToken: AUTH_TOKEN });

    const missing = await handler(new Request("http://127.0.0.1/internal/health"));
    const wrong = await handler(
      new Request("http://127.0.0.1/internal/health", { headers: { Authorization: "Bearer wrong" } })
    );

    expect(missing.status).toBe(401);
    expect(wrong.status).toBe(403);
    expect(await missing.json()).toEqual({ error: { code: "worker_auth_required" } });
    expect(await wrong.json()).toEqual({ error: { code: "worker_auth_forbidden" } });
  });
});

function expectLowercaseJson(value: string): { not: { toContainAny: (needles: string[]) => void } } {
  return {
    not: {
      toContainAny(needles: string[]) {
        for (const needle of needles) {
          expect(value).not.toContain(needle);
        }
      },
    },
  };
}

function cardFixtureHtml(candidateId: string): string {
  return `
    <article class="candidate-card" data-candidate-id="${candidateId}">
      <h2 class="candidate-title">Data Platform Engineer</h2>
      <div class="candidate-company">Redacted Analytics</div>
      <ul><li>Python</li><li>Spark</li></ul>
    </article>
  `;
}

function detailFixtureHtml(candidateId: string): string {
  return `
    <article class="candidate-detail" data-candidate-id="${candidateId}" data-detail-id="detail-${candidateId}">
      <h1 class="candidate-title">Backend Engineer</h1>
      <div class="candidate-company">Redacted Cloud</div>
      <section class="candidate-summary">Python systems</section>
    </article>
  `;
}

class FakeBrowserLauncher {
  launchCalls = 0;
  browserCloseCalls = 0;
  contextCloseCalls = 0;
  failNextNewContext = false;
  readonly contextOptions: Array<{ storageState?: unknown }> = [];

  constructor(private readonly fallbackHtml: string) {}

  async launch(): Promise<FakeBrowser> {
    this.launchCalls += 1;
    return new FakeBrowser(this);
  }

  contextStorageCookies(): string[] {
    return this.contextOptions.map((options) => {
      const state = options.storageState as { cookies?: Array<{ value?: string }> } | undefined;
      return String(state?.cookies?.[0]?.value ?? "");
    });
  }

  makePage(): FakePage {
    return new FakePage(this.fallbackHtml);
  }
}

class FakeBrowser {
  constructor(private readonly launcher: FakeBrowserLauncher) {}

  async newContext(options: { storageState?: unknown }): Promise<FakeContext> {
    if (this.launcher.failNextNewContext) {
      this.launcher.failNextNewContext = false;
      throw new Error("browser_context_failed");
    }
    this.launcher.contextOptions.push(options);
    return new FakeContext(this.launcher);
  }

  async close(): Promise<void> {
    this.launcher.browserCloseCalls += 1;
  }

  isConnected(): boolean {
    return true;
  }
}

class FakeContext {
  constructor(private readonly launcher: FakeBrowserLauncher) {}

  async newPage(): Promise<FakePage> {
    return this.launcher.makePage();
  }

  async close(): Promise<void> {
    this.launcher.contextCloseCalls += 1;
  }
}

class FakePage {
  private html: string;

  constructor(fallbackHtml: string) {
    this.html = fallbackHtml;
  }

  async goto(url: string): Promise<void> {
    const encodedHtml = url.startsWith("data:") ? url.split(",", 2)[1] : undefined;
    if (encodedHtml !== undefined) {
      this.html = decodeURIComponent(encodedHtml);
    }
  }

  async content(): Promise<string> {
    return this.html;
  }
}
