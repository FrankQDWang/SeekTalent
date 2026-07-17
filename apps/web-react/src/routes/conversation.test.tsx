import { afterEach, describe, expect, it, vi } from "vitest";
import { optimisticRequirementActionEvents } from "./conversation";
import {
  clearPendingInitialTurn,
  readPendingInitialTurn,
  writePendingInitialTurn,
} from "../lib/pendingInitialTurn";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("pending initial turn recovery", () => {
  it("restores the same idempotency key after a page reload", () => {
    const pending = {
      idempotencyKey: "create-persisted",
      message: "上海 AI 平台工程师",
      startedAt: new Date().toISOString(),
    };

    writePendingInitialTurn(pending);

    expect(readPendingInitialTurn()).toEqual(pending);
    clearPendingInitialTurn("create-persisted");
    expect(readPendingInitialTurn()).toBeNull();
  });

  it("ignores malformed pending state instead of inventing a new operation", () => {
    localStorage.setItem(
      "seektalent.workbench-v2.pending-initial-turn",
      "{broken",
    );

    expect(readPendingInitialTurn()).toBeNull();
    expect(
      localStorage.getItem("seektalent.workbench-v2.pending-initial-turn"),
    ).toBeNull();
  });

  it("removes expired pending state instead of unexpectedly restarting old work", () => {
    writePendingInitialTurn({
      idempotencyKey: "create-expired",
      message: "一小时前的招聘需求",
      startedAt: new Date(Date.now() - 60 * 60 * 1000 - 1).toISOString(),
    });

    expect(readPendingInitialTurn()).toBeNull();
  });

  it("reports when pending state cannot be persisted", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage disabled");
    });

    expect(
      writePendingInitialTurn({
        idempotencyKey: "create-unpersisted",
        message: "上海 AI 平台工程师",
        startedAt: new Date().toISOString(),
      }),
    ).toBe(false);
  });
});

describe("optimisticRequirementActionEvents", () => {
  it("shows supplemental requirement extraction immediately when confirming with text", () => {
    const events = optimisticRequirementActionEvents({
      conversationId: "agentv2_1",
      idempotencyKey: "action-1",
      payload: {
        action: "confirm",
        text: "要熟悉 Claude、Cursor、Codex",
      },
      step: 7,
    });

    expect(events).toMatchObject([
      {
        eventId: "optimistic:agentv2_1:action-1:requirement-supplement-user",
        step: 7,
        type: "user_message",
        role: "user",
        status: "pending",
        payload: { text: "要熟悉 Claude、Cursor、Codex" },
      },
      {
        eventId: "optimistic:agentv2_1:action-1:requirement-supplement-status",
        step: 8,
        type: "assistant_status",
        role: "assistant",
        status: "running",
        payload: {
          phase: "requirement_amendment",
          text: "正在根据补充要求更新需求，请稍候。",
        },
      },
    ]);
  });

  it("does not add supplemental transcript events for checkbox updates or empty confirms", () => {
    expect(
      optimisticRequirementActionEvents({
        conversationId: "agentv2_1",
        idempotencyKey: "action-1",
        payload: { action: "set_selected", itemId: "item_1", selected: false },
        step: 7,
      }),
    ).toEqual([]);
    expect(
      optimisticRequirementActionEvents({
        conversationId: "agentv2_1",
        idempotencyKey: "action-2",
        payload: { action: "confirm", text: "  " },
        step: 7,
      }),
    ).toEqual([]);
  });
});
