import { describe, expect, it } from "bun:test";

import { findBoundaryViolationsInSource } from "../scripts/checkBoundaries";

describe("liepin worker boundary checker", () => {
  it("rejects Playwright request APIs and OpenCLI imports", () => {
    const source = `
      import { request, type APIRequestContext } from "playwright";
      import * as pw from "playwright";
      import * as testPw from "@playwright/test";
      import { OpenCLI } from "@opencli/sdk";

      type Client = pw.APIRequestContext;
      type TestClient = testPw.APIRequestContext;

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
    expect(
      violations.some((violation) => violation.expression === "testPw.APIRequestContext")
    ).toBeTrue();
  });

  it("rejects destructured Playwright request access", () => {
    const source = `
      async function run(page: any, browserContext: any, context: any, playwright: any) {
        const { request } = page;
        const { request: browserRequest } = browserContext;
        const { request: contextRequest } = context;
        const { request: playwrightRequest } = playwright;

        await request.get("https://example.test");
        await browserRequest.post("https://example.test");
        await contextRequest.fetch("https://example.test");
        await playwrightRequest.newContext();
      }
    `;

    const violations = findBoundaryViolationsInSource(source, "destructured.ts");

    expect(
      violations.filter((violation) => violation.rule === "playwright-bound-request")
    ).toHaveLength(4);
  });

  it("rejects dynamic OpenCLI imports", () => {
    const source = `
      export async function run() {
        const opencli = await import("@opencli/sdk");
        const legacy = require("@opencli/sdk");
        return [opencli, legacy];
      }
    `;

    const violations = findBoundaryViolationsInSource(source, "dynamic-opencli.ts");

    expect(violations.filter((violation) => violation.rule === "opencli-import")).toHaveLength(2);
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
