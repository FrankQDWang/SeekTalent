import { z } from "zod";

export const REDACTION_POLICY_VERSION = "liepin-fixture-redaction-v1" as const;
export const REDACTED_VALUE = "[REDACTED]" as const;

const RedactionManifestSchema = z.object({
  redaction_policy_version: z.literal(REDACTION_POLICY_VERSION),
  redaction_passed: z.literal(true),
  unsafe_reasons: z.array(z.string()).length(0),
});

export type RedactionManifest = z.infer<typeof RedactionManifestSchema>;

export type RedactionResult = {
  payload: any;
  manifest: RedactionManifest;
};

const DIRECT_SENSITIVE_KEYS = new Set([
  "name",
  "candidatename",
  "realname",
  "phone",
  "phonenumber",
  "mobile",
  "mobilenumber",
  "email",
  "wechat",
  "wechatid",
  "weixin",
  "weixinid",
  "token",
  "accesstoken",
  "refreshtoken",
  "authorization",
  "cookie",
  "setcookie",
  "password",
  "secret",
]);

const WHOLE_VALUE_SENSITIVE_KEYS = new Set([
  "headers",
  "cookies",
  "storagestate",
  "localstorage",
  "sessionstorage",
]);

const EMAIL_PATTERN = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
const CHINA_MOBILE_PATTERN = /(?<!\d)1[3-9]\d{9}(?!\d)/g;
const CHINA_LANDLINE_PATTERN = /(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)/g;
const WECHAT_PATTERN =
  /(?:wxid_[A-Za-z0-9_-]+|(?:微信|weixin|wechat)[:：\s]*[A-Za-z][A-Za-z0-9_-]{4,})/gi;
const URL_PATTERN = /\bhttps?:\/\/[^\s"'<>]+/gi;
const DEBUG_WEB_SOCKET_PATTERN = /^wss?:\/\/.*(?:devtools|debug|token=)/i;
const ID_LIKE_PATTERN = /^[A-Z0-9][A-Z0-9_-]{5,}$/i;

export function redactFixturePayload(payload: unknown): RedactionResult {
  return {
    payload: redactValue(payload),
    manifest: RedactionManifestSchema.parse({
      redaction_policy_version: REDACTION_POLICY_VERSION,
      redaction_passed: true,
      unsafe_reasons: [],
    }),
  };
}

function redactValue(value: unknown, key?: string, insideIdentity = false): unknown {
  const normalizedKey = normalizeKey(key);
  const identityScope = insideIdentity || isIdentitySensitiveKey(normalizedKey);

  if (isWholeValueSensitiveKey(normalizedKey)) {
    return REDACTED_VALUE;
  }

  if (Array.isArray(value)) {
    return value.map((item) => redactValue(item, undefined, identityScope));
  }

  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([entryKey, entryValue]) => [
        entryKey,
        redactValue(entryValue, entryKey, identityScope),
      ])
    );
  }

  if (typeof value !== "string") {
    return value;
  }

  if (isDirectSensitiveKey(normalizedKey)) {
    return REDACTED_VALUE;
  }

  if (identityScope && ID_LIKE_PATTERN.test(value)) {
    return REDACTED_VALUE;
  }

  if (DEBUG_WEB_SOCKET_PATTERN.test(value)) {
    return REDACTED_VALUE;
  }

  return redactText(value);
}

function redactText(value: string): string {
  return value
    .replace(URL_PATTERN, redactUrl)
    .replace(EMAIL_PATTERN, REDACTED_VALUE)
    .replace(CHINA_MOBILE_PATTERN, REDACTED_VALUE)
    .replace(CHINA_LANDLINE_PATTERN, REDACTED_VALUE)
    .replace(WECHAT_PATTERN, REDACTED_VALUE);
}

function redactUrl(rawUrl: string): string {
  try {
    const parsed = new URL(rawUrl);
    if (!parsed.search) {
      return rawUrl;
    }
    return `${parsed.origin}${parsed.pathname}?[REDACTED_QUERY]${parsed.hash}`;
  } catch {
    const queryIndex = rawUrl.indexOf("?");
    if (queryIndex === -1) {
      return rawUrl;
    }
    return `${rawUrl.slice(0, queryIndex)}?[REDACTED_QUERY]`;
  }
}

function normalizeKey(key: string | undefined): string {
  return key?.replace(/[^a-z0-9]/gi, "").toLowerCase() ?? "";
}

function isDirectSensitiveKey(normalizedKey: string): boolean {
  return DIRECT_SENSITIVE_KEYS.has(normalizedKey);
}

function isWholeValueSensitiveKey(normalizedKey: string): boolean {
  return WHOLE_VALUE_SENSITIVE_KEYS.has(normalizedKey);
}

function isIdentitySensitiveKey(normalizedKey: string): boolean {
  return (
    normalizedKey.includes("idcard") ||
    normalizedKey.includes("identity") ||
    normalizedKey.includes("nationalid") ||
    normalizedKey.includes("passport") ||
    normalizedKey.includes("credential")
  );
}
