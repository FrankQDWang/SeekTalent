import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "bun:test";

import { createWorkerFetchHandler, createWorkerFetchHandlerFromEnv } from "../src/server";
import { EncryptedSessionStore, type SessionScope } from "../src/sessionStore";

const AUTH_TOKEN = "unit-worker-token";
const AUTH_HEADERS = { Authorization: `Bearer ${AUTH_TOKEN}` };
const SCOPE: SessionScope = {
  tenantId: "tenant-a",
  workspaceId: "workspace-a",
  providerAccountHash: "acct-hash",
  connectionId: "conn-1",
};

describe("internal Liepin worker server", () => {
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

  it("revokes encrypted session state and returns domain status only", async () => {
    const rootDir = await mkdtemp(join(tmpdir(), "liepin-worker-server-"));
    const store = new EncryptedSessionStore(rootDir, {
      keyId: "unit-key",
      keyMaterial: "unit-test-key-material",
    });
    await store.writeStorageState(SCOPE, { cookies: [{ name: "lt", value: "secret" }], origins: [] });
    const handler = createWorkerFetchHandler({ authToken: AUTH_TOKEN, sessionStore: store });

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

  it("requires a preapproved idempotency key for detail open and rejects budget decisions", async () => {
    const handler = createWorkerFetchHandler({
      authToken: AUTH_TOKEN,
      sessionStatus: { connectionId: "conn-1", status: "ready", fixtureOnly: false },
      detailOpenKeyApproved: (key) => key === "detail-approved",
    });

    const missingKey = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ connectionId: "conn-1", candidateKey: "candidate-1" }),
      })
    );
    expect(missingKey.status).toBe(400);
    expect(await missingKey.json()).toEqual({ error: { code: "missing_preapproved_idempotency_key" } });

    const unapprovedKey = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json", "x-idempotency-key": "arbitrary-key" },
        body: JSON.stringify({ connectionId: "conn-1", candidateKey: "candidate-1" }),
      })
    );
    expect(unapprovedKey.status).toBe(403);
    expect(await unapprovedKey.json()).toEqual({ error: { code: "unapproved_idempotency_key" } });

    const approvedKey = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json", "x-idempotency-key": "detail-approved" },
        body: JSON.stringify({ connectionId: "conn-1", candidateKey: "candidate-1" }),
      })
    );
    expect(approvedKey.status).toBe(200);
    expect(await approvedKey.json()).toEqual({ status: "accepted", idempotencyKey: "detail-approved" });

    const withBudget = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json", "x-idempotency-key": "detail-approved" },
        body: JSON.stringify({
          connectionId: "conn-1",
          candidateKey: "candidate-1",
          budgetRemaining: 10,
        }),
      })
    );
    expect(withBudget.status).toBe(400);
    expect(await withBudget.json()).toEqual({ error: { code: "budget_decision_not_allowed_in_worker" } });
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
