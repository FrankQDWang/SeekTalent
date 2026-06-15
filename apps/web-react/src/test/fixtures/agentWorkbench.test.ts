import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  agentWorkbenchArchivedViewFixture,
  agentWorkbenchCompletedViewFixture,
  agentWorkbenchLargeGraphFixture,
  agentWorkbenchPermissionDeniedViewFixture,
  agentWorkbenchRequirementReviewViewFixture,
  agentWorkbenchRunningViewFixture,
} from "./agentWorkbenchBff";

const fixtureDir = dirname(fileURLToPath(import.meta.url));

describe("agent workbench design fixture", () => {
  it("provides BFF-native workbench fixtures for Storybook and visual tests", () => {
    expect(agentWorkbenchRunningViewFixture.schemaVersion).toBe(
      "agent.workbench.view.v1",
    );
    expect(Array.isArray(agentWorkbenchRunningViewFixture.candidates)).toBe(
      true,
    );
    expect(agentWorkbenchLargeGraphFixture.nodes).toHaveLength(15);
    expect(agentWorkbenchLargeGraphFixture.edges.length).toBeGreaterThanOrEqual(
      16,
    );

    const graphText = agentWorkbenchLargeGraphFixture.nodes
      .map((node) => `${node.label} ${node.summary}`)
      .join("\n");

    for (const expectedTerm of [
      "source_result",
      "scoring",
      "detail_approval",
      "final_summary",
    ]) {
      expect(graphText).toContain(expectedTerm);
    }
  });

  it("does not build Storybook BFF fixtures through the legacy design adapter", () => {
    expect.hasAssertions();

    const bffFixtureSource = readFileSync(
      resolve(fixtureDir, "agentWorkbenchBff.ts"),
      "utf8",
    );

    expect(bffFixtureSource).not.toContain("./productionAgentWorkbench");
    expect(bffFixtureSource).not.toContain("./agentWorkbenchStates");
    expect(bffFixtureSource).not.toContain("./agentWorkbench");
    expect(bffFixtureSource).not.toContain("productionConversationFixture");
    expect(bffFixtureSource).not.toContain("FixtureConversationResponse");
    expect(existsSync(resolve(fixtureDir, "productionAgentWorkbench.ts"))).toBe(
      false,
    );
    expect(existsSync(resolve(fixtureDir, "agentWorkbenchStates.ts"))).toBe(
      false,
    );
    expect(existsSync(resolve(fixtureDir, "agentWorkbench.ts"))).toBe(false);
    expect(JSON.stringify(agentWorkbenchRunningViewFixture)).not.toContain(
      "legacy-design",
    );
  });

  it("covers the screen-level state matrix without legacy fixture adapters", () => {
    expect.hasAssertions();

    expect(agentWorkbenchRunningViewFixture.pendingActions.allowed).toContain(
      "submit_message",
    );
    expect(
      agentWorkbenchRequirementReviewViewFixture.pendingActions.allowed,
    ).toContain("confirm_requirements");
    expect(agentWorkbenchPermissionDeniedViewFixture.reasonCode).toBe(
      "permission_denied",
    );
    expect(agentWorkbenchCompletedViewFixture.finalSummary?.text).toBe(
      "第一轮推荐 2 位候选人，候选人 A 为强匹配。",
    );
    expect(agentWorkbenchArchivedViewFixture.conversation.isArchived).toBe(
      true,
    );
  });
});
