import { createHash, webcrypto } from "node:crypto";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

export type SessionScope = {
  tenantId: string;
  workspaceId: string;
  providerAccountHash: string;
  connectionId: string;
};

export type BrowserStorageState = {
  cookies?: Array<Record<string, unknown>>;
  origins?: Array<Record<string, unknown>>;
};

export type SessionStoreKey = {
  keyId: string;
  keyMaterial: string;
};

type SessionEnvelope = {
  version: 1;
  keyId: string;
  algorithm: "AES-GCM";
  nonce: string;
  ciphertext: string;
};

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

export function loadSessionStoreKeyFromEnv(env: Record<string, string | undefined>): SessionStoreKey {
  const keyId = env.liepin_session_store_key_id;
  const keyMaterial = env.liepin_session_store_key;
  if (!keyId || !keyMaterial) {
    throw new Error("Missing Liepin session store key environment.");
  }
  return { keyId, keyMaterial };
}

export class EncryptedSessionStore {
  constructor(
    private readonly rootDir: string,
    private readonly key: SessionStoreKey,
  ) {}

  sessionPath(scope: SessionScope): string {
    return join(
      this.rootDir,
      safePathPart(scope.tenantId),
      safePathPart(scope.workspaceId),
      safePathPart(scope.providerAccountHash),
      safePathPart(scope.connectionId),
      "storage-state.json.enc",
    );
  }

  async writeStorageState(scope: SessionScope, state: BrowserStorageState): Promise<string> {
    const sessionPath = this.sessionPath(scope);
    const nonce = webcrypto.getRandomValues(new Uint8Array(12));
    const aesKey = await this.importKey();
    const ciphertext = await webcrypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce },
      aesKey,
      textEncoder.encode(JSON.stringify(state)),
    );
    const envelope: SessionEnvelope = {
      version: 1,
      keyId: this.key.keyId,
      algorithm: "AES-GCM",
      nonce: toBase64(nonce),
      ciphertext: toBase64(new Uint8Array(ciphertext)),
    };

    await mkdir(dirname(sessionPath), { recursive: true });
    await writeFile(sessionPath, JSON.stringify(envelope), { encoding: "utf8", mode: 0o600 });
    return sessionPath;
  }

  async readStorageState(scope: SessionScope): Promise<BrowserStorageState> {
    const sessionPath = this.sessionPath(scope);
    let rawEnvelope: string;
    try {
      rawEnvelope = await readFile(sessionPath, "utf8");
    } catch (error) {
      throw new Error(`Liepin session state not found: ${sessionPath}`, { cause: error });
    }

    const envelope = JSON.parse(rawEnvelope) as SessionEnvelope;
    if (envelope.keyId !== this.key.keyId) {
      throw new Error(`Liepin session key ID mismatch: expected ${this.key.keyId}.`);
    }
    const aesKey = await this.importKey();
    const plaintext = await webcrypto.subtle.decrypt(
      { name: "AES-GCM", iv: fromBase64(envelope.nonce) },
      aesKey,
      fromBase64(envelope.ciphertext),
    );
    return JSON.parse(textDecoder.decode(plaintext)) as BrowserStorageState;
  }

  async revoke(scope: SessionScope): Promise<boolean> {
    try {
      await rm(this.sessionPath(scope));
      return true;
    } catch (error) {
      if (isNotFound(error)) {
        return false;
      }
      throw error;
    }
  }

  private async importKey(): Promise<CryptoKey> {
    const digest = createHash("sha256").update(this.key.keyMaterial, "utf8").digest();
    return webcrypto.subtle.importKey("raw", digest, "AES-GCM", false, ["encrypt", "decrypt"]);
  }
}

function safePathPart(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    throw new Error("Liepin session path scope cannot be empty.");
  }
  return trimmed.replaceAll(/[^A-Za-z0-9._=-]/g, "_");
}

function toBase64(value: Uint8Array): string {
  return Buffer.from(value).toString("base64");
}

function fromBase64(value: string): Uint8Array<ArrayBuffer> {
  const buffer = Buffer.from(value, "base64");
  return new Uint8Array(buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength));
}

function isNotFound(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
}
