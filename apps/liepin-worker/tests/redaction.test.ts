import { describe, expect, it } from "bun:test";

import { redactFixturePayload } from "../src/redaction";

const REDACTED = "[REDACTED]";

describe("liepin fixture redaction", () => {
  it("recursively redacts identity contact browser and debug data", () => {
    const raw = {
      candidate: {
        name: "Zhang San",
        candidateName: "Li Si",
        realName: "Wang Wu",
        profileUrl: "https://www.liepin.com/candidate/123?token=secret-token&phone=13800138000",
        html: "<div>联系我 mobile: 13800138000 weixin: wxid_html_secret</div>",
        notes:
          "email zhangsan@example.com phone 13800138000 wechat wxid_plain_secret",
        identity: {
          idCardNumber: "110105199001011234",
          nationalId: "P1234567",
        },
        nested: [
          {
            mobile: "13900139000",
            email: "lisi@example.com",
            wechat: "wxid_nested_secret",
            weixin: "nested-weixin",
          },
        ],
      },
      headers: {
        Authorization: "Bearer bearer-secret",
        Cookie: "lt_auth=cookie-secret; session=session-secret",
        "X-Debug-WebSocket": "wss://debug.example.test/session?token=header-debug-secret",
      },
      cookies: [{ name: "lt_auth", value: "cookie-secret" }],
      token: "top-level-token",
      storageState: {
        cookies: [{ name: "sid", value: "storage-cookie-secret" }],
        origins: [
          {
            origin: "https://www.liepin.com",
            localStorage: [{ name: "access-token", value: "local-storage-secret" }],
            sessionStorage: [{ name: "refresh-token", value: "session-storage-secret" }],
          },
        ],
      },
      cdpEndpoint: "ws://127.0.0.1:9222/devtools/browser/debug-secret",
      debugWebSocketUrl: "wss://debug.example.test/session?token=debug-secret",
    };

    const result = redactFixturePayload(raw);

    expect(result.manifest).toEqual({
      redaction_policy_version: "liepin-fixture-redaction-v1",
      redaction_passed: true,
      unsafe_reasons: [],
    });

    expect(result.payload.candidate.name).toBe(REDACTED);
    expect(result.payload.candidate.candidateName).toBe(REDACTED);
    expect(result.payload.candidate.realName).toBe(REDACTED);
    expect(result.payload.candidate.identity.idCardNumber).toBe(REDACTED);
    expect(result.payload.candidate.identity.nationalId).toBe(REDACTED);
    expect(result.payload.candidate.nested[0].mobile).toBe(REDACTED);
    expect(result.payload.candidate.nested[0].email).toBe(REDACTED);
    expect(result.payload.candidate.nested[0].wechat).toBe(REDACTED);
    expect(result.payload.candidate.nested[0].weixin).toBe(REDACTED);
    expect(result.payload.headers).toBe(REDACTED);
    expect(result.payload.cookies).toBe(REDACTED);
    expect(result.payload.token).toBe(REDACTED);
    expect(result.payload.storageState).toBe(REDACTED);
    expect(result.payload.cdpEndpoint).toBe(REDACTED);
    expect(result.payload.debugWebSocketUrl).toBe(REDACTED);

    const serialized = JSON.stringify(result.payload);
    for (const unsafe of [
      "Zhang San",
      "Li Si",
      "Wang Wu",
      "zhangsan@example.com",
      "lisi@example.com",
      "13800138000",
      "13900139000",
      "wxid_html_secret",
      "wxid_plain_secret",
      "wxid_nested_secret",
      "110105199001011234",
      "P1234567",
      "secret-token",
      "bearer-secret",
      "cookie-secret",
      "local-storage-secret",
      "session-storage-secret",
      "debug-secret",
      "header-debug-secret",
    ]) {
      expect(serialized).not.toContain(unsafe);
    }
    expect(serialized).not.toContain("?token=");
  });

  it("redacts landline phones identity-scoped IDs and generic debug websocket values", () => {
    const result = redactFixturePayload({
      html: "<section>联系电话: 010-12345678</section>",
      notes: "mobile backup 021-87654321",
      identity: {
        id: "110105199001011234",
      },
      endpoint: "ws://127.0.0.1:9222/devtools/browser/generic-secret",
      cdpEndpoint: "http://127.0.0.1:9222/json/version",
    });

    const serialized = JSON.stringify(result.payload);

    expect(serialized).not.toContain("010-12345678");
    expect(serialized).not.toContain("021-87654321");
    expect(result.payload.identity.id).toBe(REDACTED);
    expect(result.payload.endpoint).toBe(REDACTED);
    expect(result.payload.cdpEndpoint).toBe(REDACTED);
    expect(serialized).not.toContain("110105199001011234");
    expect(serialized).not.toContain("generic-secret");
    expect(serialized).not.toContain("9222/json/version");
  });
});
