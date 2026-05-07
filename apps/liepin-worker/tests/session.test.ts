import { describe, expect, it } from "bun:test";

import {
  createLoginHandoff,
  LIEPIN_SESSION_STATUSES,
  type ManagedLoginStatus,
} from "../src/session";

describe("managed Liepin login contract", () => {
  it("defines the worker session statuses", () => {
    const statuses: ManagedLoginStatus[] = [
      "logged_out",
      "ready",
      "needs_user_action",
      "risk_control_wait",
      "temporarily_rate_limited",
      "failed",
    ];

    expect([...LIEPIN_SESSION_STATUSES]).toEqual(statuses);
  });

  it("returns an opaque handoff without worker browser internals", () => {
    const handoff = createLoginHandoff({
      connectionId: "conn_abc",
      handoffToken: "opaque-handoff-token",
      expiresAt: new Date("2026-05-07T12:00:00.000Z"),
    });

    expect(handoff).toEqual({
      connection_id: "conn_abc",
      handoff_token: "opaque-handoff-token",
      browser_view_url: null,
      expires_at: "2026-05-07T12:00:00Z",
      status_event_stream: "/api/liepin/connections/conn_abc/events",
    });

    const serialized = JSON.stringify(handoff).toLowerCase();
    for (const forbidden of [
      "cdp",
      "remote debugging",
      "playwright",
      "websocket",
      "storagestate",
      "storage_state",
      "worker",
      "base_url",
    ]) {
      expect(serialized).not.toContain(forbidden);
    }
  });
});
