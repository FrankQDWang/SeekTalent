import { describe, expect, it } from "vitest";
import { optimisticRequirementActionEvents } from "./conversation";

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
