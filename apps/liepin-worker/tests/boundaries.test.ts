import { describe, expect, it } from "bun:test";

import { findBoundaryViolationsInSource } from "../scripts/checkBoundaries";

describe("liepin worker boundary checker", () => {
  it("rejects Playwright request APIs and OpenCLI imports", () => {
    const source = `
      import { request, type APIRequestContext } from "playwright";
      import * as pw from "playwright";
      import { OpenCLI } from "@opencli/sdk";

      type Client = pw.APIRequestContext;

      async function run(page: any, browserContext: any, context: any, playwright: any) {
        page.request.get("https://example.test");
        browserContext.request.post("https://example.test");
        context.request.fetch("https://example.test");
        playwright.request.newContext();
        request.newContext();
        page["request"].get("https://example.test");
      }
    `;

    const violations = findBoundaryViolationsInSource(source, "fixture.ts");
    const rules = violations.map((violation) => violation.rule);

    expect(rules).toContain("playwright-api-request-context");
    expect(rules).toContain("playwright-bound-request");
    expect(rules).toContain("playwright-computed-request");
    expect(rules).toContain("playwright-request-new-context");
    expect(rules).toContain("opencli-import");
    expect(
      violations.some((violation) => violation.expression === "pw.APIRequestContext")
    ).toBeTrue();
  });

  it("allows normal browser automation without worker-side HTTP clients", () => {
    const source = `
      import { chromium } from "playwright";

      export async function run() {
        const browser = await chromium.launch();
        const page = await browser.newPage();
        await page.goto("https://www.liepin.com");
        await page.locator("text=Login").click();
        await browser.close();
      }
    `;

    expect(findBoundaryViolationsInSource(source, "allowed.ts")).toEqual([]);
  });
});
