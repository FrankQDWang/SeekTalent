import { describe, expect, expectTypeOf, it } from "vitest";
import { api } from "./client";

describe("Workbench BFF API client boundary", () => {
  it("does not compile raw agent or legacy workbench route calls", () => {
    const assertClientBoundary = () => {
      void api.GET("/api/agent/workbench/conversations");
      // @ts-expect-error Frontend Workbench code must not call raw agent routes.
      void api.GET("/api/agent/conversations");
      // @ts-expect-error Frontend Workbench code must not call legacy workbench routes.
      void api.GET("/api/workbench/sessions");
    };

    expect(assertClientBoundary).toBeTypeOf("function");
    expectTypeOf(assertClientBoundary).toEqualTypeOf<() => void>();
  });
});
