import { createHmac } from "node:crypto";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "bun:test";

import { createWorkerFetchHandler, createWorkerFetchHandlerFromEnv } from "../src/server";
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
  detailUrl: string;
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
    detailUrl: input.detailUrl,
  };
  const encodedPayload = Buffer.from(JSON.stringify(sortObjectKeys(payload)), "utf8").toString("base64url");
  const signature = createHmac("sha256", DETAIL_APPROVAL_SECRET).update(encodedPayload).digest("base64url");
  return `detail-open:v1:${encodedPayload}.${signature}`;
}

function sortObjectKeys(payload: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(payload).sort(([left], [right]) => left.localeCompare(right)));
}

describe("Liepin worker security boundaries", () => {
  it("does not turn caller-provided session scope hashes into observed account identity", async () => {
    const rootDir = await mkdtemp(join(tmpdir(), "liepin-worker-status-scope-"));
    const store = new EncryptedSessionStore(rootDir, {
      keyId: "env-key",
      keyMaterial: "env-test-key-material",
    });
    await store.writeStorageState({ ...SCOPE, providerAccountHash: "caller-hash" }, { cookies: [], origins: [] });
    const handler = createWorkerFetchHandler({ authToken: AUTH_TOKEN, sessionStore: store });

    const response = await handler(
      new Request(
        "http://127.0.0.1/internal/session/status?tenantId=tenant-a&workspaceId=workspace-a&providerAccountHash=caller-hash&connectionId=conn-1",
        { headers: AUTH_HEADERS }
      )
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      connectionId: "conn-1",
      status: "ready",
      fixtureOnly: false,
    });
  });

  it("rejects detail approvals when the signed detail URL does not match the request URL", async () => {
    const handler = createWorkerFetchHandlerFromEnv({
      NODE_ENV: "test",
      SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN: AUTH_TOKEN,
      SEEKTALENT_LIEPIN_SESSION_STORE_DIR: await mkdtemp(join(tmpdir(), "liepin-worker-url-approval-")),
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID: "env-key",
      SEEKTALENT_LIEPIN_SESSION_STORE_KEY: "env-test-key-material",
      SEEKTALENT_LIEPIN_DETAIL_OPEN_APPROVAL_SECRET: DETAIL_APPROVAL_SECRET,
      SEEKTALENT_LIEPIN_WORKER_TEST_ALLOW_DATA_DETAIL_URLS: "1",
    });
    const approvedDetailUrl = "https://www.liepin.com/candidate/approved";
    const tamperedDetailUrl = "https://www.liepin.com/candidate/tampered";

    const response = await handler(
      new Request("http://127.0.0.1/internal/details/open", {
        method: "POST",
        headers: { ...AUTH_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          ...SCOPE,
          providerDayKey: PROVIDER_DAY_KEY,
          workerCommandId: "cmd-url-mismatch",
          requests: [
            {
              requestId: "request-url-mismatch",
              attemptId: "attempt-url-mismatch",
              idempotencyKey: "open:env-candidate-1",
              approvalKey: detailApprovalKey({
                ...SCOPE,
                providerDayKey: PROVIDER_DAY_KEY,
                candidateId: "env-candidate-1",
                idempotencyKey: "open:env-candidate-1",
                detailUrl: approvedDetailUrl,
              }),
              candidateId: "env-candidate-1",
              detailUrl: tamperedDetailUrl,
            },
          ],
        }),
      })
    );

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: { code: "unapproved_idempotency_key" } });
  });
});
