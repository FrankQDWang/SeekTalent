export function objectPayload(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function stringPayloadValue(value: unknown, key: string): string | null {
  const payload = objectPayload(value);
  const field = payload[key];
  return typeof field === "string" && field.trim() ? field.trim() : null;
}
