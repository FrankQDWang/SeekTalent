import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "bun:test";

describe("worker payload extraction helpers", () => {
  it("centralizes object and string payload extraction for card and detail responses", async () => {
    const helperPath = new URL("../src/payload.ts", import.meta.url);
    const cardSearchPath = new URL("../src/cardSearch.ts", import.meta.url);
    const detailPath = new URL("../src/detail.ts", import.meta.url);

    expect(existsSync(helperPath)).toBe(true);
    const helperSource = readFileSync(helperPath, "utf8");
    const cardSearchSource = readFileSync(cardSearchPath, "utf8");
    const detailSource = readFileSync(detailPath, "utf8");

    expect(helperSource).toContain("export function objectPayload");
    expect(helperSource).toContain("export function stringPayloadValue");
    expect(cardSearchSource).toContain('from "./payload"');
    expect(detailSource).toContain('from "./payload"');
    expect(cardSearchSource).not.toContain("function objectPayload");
    expect(cardSearchSource).not.toContain("function stringPayloadValue");
    expect(detailSource).not.toContain("function objectPayload");
    expect(detailSource).not.toContain("function stringPayloadValue");

    const { objectPayload, stringPayloadValue } = await import("../src/payload");

    expect(objectPayload(null)).toEqual({});
    expect(objectPayload(["listing-1"])).toEqual({});
    expect(objectPayload({ listingId: "  listing-1  ", count: 2 })).toEqual({
      listingId: "  listing-1  ",
      count: 2,
    });
    expect(stringPayloadValue({ listingId: "  listing-1  " }, "listingId")).toBe("listing-1");
    expect(stringPayloadValue({ listingId: "   " }, "listingId")).toBeNull();
    expect(stringPayloadValue({ listingId: 123 }, "listingId")).toBeNull();
    expect(stringPayloadValue(["listing-1"], "listingId")).toBeNull();
  });
});
