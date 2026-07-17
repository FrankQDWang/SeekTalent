const STORAGE_KEY = "seektalent.workbench-v2.pending-initial-turn";
const MAX_PENDING_AGE_MS = 60 * 60 * 1000;

export type PendingInitialTurn = {
  idempotencyKey: string;
  message: string;
  startedAt: string;
};

export function readPendingInitialTurn(): PendingInitialTurn | null {
  try {
    const raw = globalThis.localStorage.getItem(STORAGE_KEY);
    if (raw === null) {
      return null;
    }
    const value: unknown = JSON.parse(raw);
    if (!isPendingInitialTurn(value)) {
      removePendingInitialTurn();
      return null;
    }
    return value;
  } catch {
    removePendingInitialTurn();
    return null;
  }
}

export function writePendingInitialTurn(pending: PendingInitialTurn): boolean {
  try {
    globalThis.localStorage.setItem(STORAGE_KEY, JSON.stringify(pending));
    return true;
  } catch {
    return false;
  }
}

export function clearPendingInitialTurn(idempotencyKey: string): void {
  if (readPendingInitialTurn()?.idempotencyKey === idempotencyKey) {
    removePendingInitialTurn();
  }
}

function removePendingInitialTurn(): void {
  try {
    globalThis.localStorage.removeItem(STORAGE_KEY);
  } catch {
    return;
  }
}

function isPendingInitialTurn(value: unknown): value is PendingInitialTurn {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  const startedAt =
    typeof record.startedAt === "string" ? Date.parse(record.startedAt) : NaN;
  return (
    typeof record.idempotencyKey === "string" &&
    record.idempotencyKey.length > 0 &&
    typeof record.message === "string" &&
    record.message.length > 0 &&
    Number.isFinite(startedAt) &&
    startedAt <= Date.now() &&
    Date.now() - startedAt <= MAX_PENDING_AGE_MS
  );
}
