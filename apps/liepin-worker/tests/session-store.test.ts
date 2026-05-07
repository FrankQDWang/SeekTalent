import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { join, relative, resolve } from "node:path";
import { tmpdir } from "node:os";

import {
  EncryptedSessionStore,
  loadSessionStoreKeyFromEnv,
  type BrowserStorageState,
  type SessionScope,
} from "../src/sessionStore";

const OLD_ENV = { ...Bun.env };

describe("encrypted Liepin session store", () => {
  let rootDir: string;

  beforeEach(async () => {
    rootDir = await mkdtemp(join(tmpdir(), "liepin-session-store-"));
    Bun.env.liepin_session_store_key_id = "key-v1";
    Bun.env.liepin_session_store_key = "unit-test-secret-value";
  });

  afterEach(async () => {
    await rm(rootDir, { recursive: true, force: true });
    Bun.env.liepin_session_store_key_id = OLD_ENV.liepin_session_store_key_id;
    Bun.env.liepin_session_store_key = OLD_ENV.liepin_session_store_key;
  });

  it("encrypts storage state before writing it to disk", async () => {
    const store = new EncryptedSessionStore(rootDir, loadSessionStoreKeyFromEnv(Bun.env));
    const state: BrowserStorageState = {
      cookies: [{ name: "lt_auth", value: "cookie-secret", domain: ".liepin.com", path: "/" }],
      origins: [
        {
          origin: "https://www.liepin.com",
          localStorage: [{ name: "recruiter-token", value: "local-secret" }],
        },
      ],
    };

    const sessionPath = await store.writeStorageState(scope(), state);
    const encryptedFile = await readFile(sessionPath, "utf8");

    expect(encryptedFile).toContain('"keyId":"key-v1"');
    expect(encryptedFile).not.toContain("lt_auth");
    expect(encryptedFile).not.toContain("cookie-secret");
    expect(encryptedFile).not.toContain("recruiter-token");
    expect(await store.readStorageState(scope())).toEqual(state);
  });

  it("fails decryption when the key ID is wrong", async () => {
    const store = new EncryptedSessionStore(rootDir, loadSessionStoreKeyFromEnv(Bun.env));
    await store.writeStorageState(scope(), { cookies: [{ name: "sid", value: "secret" }] });

    const wrongKeyIdStore = new EncryptedSessionStore(rootDir, {
      keyId: "key-v2",
      keyMaterial: "unit-test-secret-value",
    });

    await expect(wrongKeyIdStore.readStorageState(scope())).rejects.toThrow(/key id/i);
  });

  it("fails decryption when the key value is wrong", async () => {
    const store = new EncryptedSessionStore(rootDir, loadSessionStoreKeyFromEnv(Bun.env));
    await store.writeStorageState(scope(), { cookies: [{ name: "sid", value: "secret" }] });

    const wrongKeyStore = new EncryptedSessionStore(rootDir, {
      keyId: "key-v1",
      keyMaterial: "different-secret-value",
    });

    await expect(wrongKeyStore.readStorageState(scope())).rejects.toThrow();
  });

  it("deletes encrypted state on revoke", async () => {
    const store = new EncryptedSessionStore(rootDir, loadSessionStoreKeyFromEnv(Bun.env));
    await store.writeStorageState(scope(), { cookies: [{ name: "sid", value: "secret" }] });

    expect(await store.revoke(scope())).toBe(true);
    expect(await store.revoke(scope())).toBe(false);
    await expect(store.readStorageState(scope())).rejects.toThrow(/not found/i);
  });

  it("namespaces session paths by tenant workspace account and connection", () => {
    const store = new EncryptedSessionStore(rootDir, loadSessionStoreKeyFromEnv(Bun.env));

    expect(store.sessionPath(scope())).toBe(
      join(
        rootDir,
        encoded("tenant-a"),
        encoded("workspace-a"),
        encoded("account-hash-a"),
        encoded("conn_abc"),
        "storage-state.json.enc"
      )
    );
  });

  it("keeps traversal-like scope parts inside the session root", () => {
    const store = new EncryptedSessionStore(rootDir, loadSessionStoreKeyFromEnv(Bun.env));
    const sessionPath = store.sessionPath({ ...scope(), tenantId: ".." });
    const relativePath = relative(resolve(rootDir), resolve(sessionPath));

    expect(relativePath).not.toStartWith("..");
    expect(resolve(sessionPath)).toStartWith(resolve(rootDir));
  });

  it("keeps slash and underscore scope values in distinct session paths", () => {
    const store = new EncryptedSessionStore(rootDir, loadSessionStoreKeyFromEnv(Bun.env));

    expect(store.sessionPath({ ...scope(), tenantId: "a/b" })).not.toBe(
      store.sessionPath({ ...scope(), tenantId: "a_b" })
    );
  });
});

function scope(): SessionScope {
  return {
    tenantId: "tenant-a",
    workspaceId: "workspace-a",
    providerAccountHash: "account-hash-a",
    connectionId: "conn_abc",
  };
}

function encoded(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}
