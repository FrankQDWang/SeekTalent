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
        const { ["request"]: guardedRequest } = page;

        await request.get("https://example.test");
        await browserRequest.post("https://example.test");
        await contextRequest.fetch("https://example.test");
        await playwrightRequest.newContext();
        await guardedRequest.get("https://example.test");
      }
    `;

    const violations = findBoundaryViolationsInSource(source, "destructured.ts");

    expect(
      violations.filter((violation) => violation.rule === "playwright-bound-request")
    ).toHaveLength(5);
  });

  it("rejects dynamic OpenCLI imports", () => {
    const source = `
      export async function run() {
        const opencli = await import("@opencli/sdk");
        const legacy = require("@opencli/sdk");
        const templateOpencli = await import(\`@opencli/sdk\`);
        const templateLegacy = require(\`@opencli/sdk\`);
        return [opencli, legacy, templateOpencli, templateLegacy];
      }
    `;

    const violations = findBoundaryViolationsInSource(source, "dynamic-opencli.ts");

    expect(violations.filter((violation) => violation.rule === "opencli-import")).toHaveLength(4);
  });

  it("rejects network inspection, interception, script evaluation, cookies, storage, CDP, and raw in-page network calls", () => {
    const source = `
      async function run(
        tools: any,
        page: any,
        browserContext: any,
        route: any,
        locator: any,
        elementHandle: any
      ) {
        tools.list_network_requests();
        tools.get_network_request("request_1");
        tools.evaluate_script("document.cookie");
        list_network_requests();
        get_network_request("request_1");
        evaluate_script("document.cookie");

        await page.route("**/api/**", route => route.fetch());
        await browserContext.route("**/api/**", route => route.continue({ headers: {} }));
        await route.fetch();
        await route.continue({ method: "POST" });
        await route.fulfill({ body: "raw" });
        await page.waitForResponse("**/api/**");
        page.on("request", request => console.log(request.url()));
        page.on("response", response => console.log(response.url()));

        await page.evaluate(() => fetch("/api/resume"));
        await page.evaluateHandle(() => document.cookie);
        await locator.evaluate(node => node.textContent);
        await elementHandle.evaluate(node => node.textContent);
        await page.addInitScript(() => localStorage.setItem("x", "y"));
        await browserContext.addInitScript(() => sessionStorage.clear());
        await browserContext.addCookies([]);
        await browserContext.setExtraHTTPHeaders({});
        await browserContext.storageState({ path: "auth.json" });
        await browserContext.newCDPSession(page);

        fetch("/api/resume");
        new XMLHttpRequest();
      }
    `;

    const violations = findBoundaryViolationsInSource(source, "src/cardSearch.ts");
    const expressions = violations.map((violation) => violation.expression);

    for (const expected of [
      "tools.list_network_requests",
      "tools.get_network_request",
      "tools.evaluate_script",
      "list_network_requests",
      "get_network_request",
      "evaluate_script",
      "page.route",
      "browserContext.route",
      "route.fetch",
      "route.continue",
      "route.fulfill",
      "page.waitForResponse",
      "page.on",
      "page.evaluate",
      "page.evaluateHandle",
      "locator.evaluate",
      "elementHandle.evaluate",
      "page.addInitScript",
      "browserContext.addInitScript",
      "browserContext.addCookies",
      "browserContext.setExtraHTTPHeaders",
      "browserContext.storageState",
      "browserContext.newCDPSession",
      "fetch",
      "XMLHttpRequest",
    ]) {
      expect(expressions).toContain(expected);
    }
  });

  it("rejects aliased request owners and computed provider action methods", () => {
    const source = `
      async function run(page: any, browserContext: any) {
        const ctx = browserContext;
        const p = page;

        await ctx.request.get("/api/resume");
        await p["route"]("**/api/**", route => route.fetch());
        await p["evaluate"](() => document.cookie);
        await browserContext["newCDPSession"](page);
      }
    `;

    const violations = findBoundaryViolationsInSource(source, "src/cardSearch.ts");
    const expressions = violations.map((violation) => violation.expression);

    expect(expressions).toContain("ctx.request");
    expect(expressions).toContain('p["route"]');
    expect(expressions).toContain('p["evaluate"]');
    expect(expressions).toContain('browserContext["newCDPSession"]');
  });

  it("uses scan profiles so session lifecycle storageState remains allowed but provider action storage primitives do not", () => {
    const sessionLifecycleSource = `
      async function complete(session: any, contextOptions: any, storageState: any) {
        const state = await session.context.storageState();
        contextOptions.storageState = storageState;
        return state;
      }
    `;
    expect(findBoundaryViolationsInSource(sessionLifecycleSource, "src/loginRelay.ts")).toEqual([]);

    const providerActionSource = `
      async function run(browserContext: any) {
        await browserContext.storageState({ path: "auth.json" });
        fetch("/api/resume");
        new XMLHttpRequest();
      }
    `;
    const violations = findBoundaryViolationsInSource(providerActionSource, "src/cardSearch.ts");
    const expressions = violations.map((violation) => violation.expression);

    expect(expressions).toContain("browserContext.storageState");
    expect(expressions).toContain("fetch");
    expect(expressions).toContain("XMLHttpRequest");

    const serverProviderActionSource = `
      import { searchCards } from "./cardSearch";

      async function createProductionCardSearchHandler(browserContext: any) {
        await browserContext.storageState({ path: "auth.json" });
        fetch("/api/resume");
        new XMLHttpRequest();
        return searchCards;
      }
    `;
    const serverViolations = findBoundaryViolationsInSource(serverProviderActionSource, "src/server.ts");
    const serverExpressions = serverViolations.map((violation) => violation.expression);

    expect(serverExpressions).toContain("browserContext.storageState");
    expect(serverExpressions).toContain("fetch");
    expect(serverExpressions).toContain("XMLHttpRequest");
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
