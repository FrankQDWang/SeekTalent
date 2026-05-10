import { chromium, type Browser, type BrowserContext, type Page } from "playwright";

import { createInternalLoginHandoff, type InternalLoginHandoff } from "./session";
import { EncryptedSessionStore, type BrowserStorageState, type SessionScope } from "./sessionStore";

export type LoginRelayStartRequest = {
  connectionId: string;
  handoffToken: string;
  expiresAt: Date;
  scope?: SessionScope;
};

export type LoginRelayInputRequest = {
  connectionId: string;
  action: "click" | "type" | "key";
  x?: number;
  y?: number;
  text?: string;
  key?: string;
};

export type LoginRelaySnapshot = {
  connectionId: string;
  status: "login_in_progress" | "ready" | "expired" | "failed";
  pageTitle: string;
  pageOrigin: string;
  imageMimeType: "image/jpeg";
  imageBase64: string;
  updatedAt: string;
};

export type LoginRelayInputResult = {
  connectionId: string;
  accepted: true;
  updatedAt: string;
};

export type LoginRelayCompleteResult = {
  connectionId: string;
  status: "ready";
  providerAccountHash?: string;
  fixtureOnly: false;
};

export interface LoginRelayController {
  start(request: LoginRelayStartRequest): Promise<InternalLoginHandoff>;
  snapshot(connectionId: string): Promise<LoginRelaySnapshot>;
  input(request: LoginRelayInputRequest): Promise<LoginRelayInputResult>;
  complete(connectionId: string): Promise<LoginRelayCompleteResult>;
}

export class LoginRelayNotVerifiedError extends Error {
  constructor() {
    super("Liepin login has not been verified.");
    this.name = "LoginRelayNotVerifiedError";
  }
}

type RelaySession = {
  browser: Browser;
  context: BrowserContext;
  page: Page;
  expiresAt: Date;
  scope?: SessionScope;
};

export class PlaywrightLoginRelayController implements LoginRelayController {
  private readonly sessions = new Map<string, RelaySession>();

  constructor(private readonly sessionStore: EncryptedSessionStore) {}

  async start(request: LoginRelayStartRequest): Promise<InternalLoginHandoff> {
    await this.close(request.connectionId);
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1280, height: 820 } });
    const page = await context.newPage();
    await page.goto("https://www.liepin.com/", { waitUntil: "domcontentloaded", timeout: 30_000 }).catch(() => undefined);
    const session: RelaySession = {
      browser,
      context,
      page,
      expiresAt: request.expiresAt,
    };
    if (request.scope !== undefined) {
      session.scope = request.scope;
    }
    this.sessions.set(request.connectionId, session);
    return createInternalLoginHandoff(request);
  }

  async snapshot(connectionId: string): Promise<LoginRelaySnapshot> {
    const session = this.requireLiveSession(connectionId);
    const image = await session.page.screenshot({ type: "jpeg", quality: 72, fullPage: false });
    return {
      connectionId,
      status: "login_in_progress",
      pageTitle: await safePageTitle(session.page),
      pageOrigin: safePageOrigin(session.page.url()),
      imageMimeType: "image/jpeg",
      imageBase64: image.toString("base64"),
      updatedAt: new Date().toISOString(),
    };
  }

  async input(request: LoginRelayInputRequest): Promise<LoginRelayInputResult> {
    const session = this.requireLiveSession(request.connectionId);
    if (request.action === "click") {
      if (typeof request.x !== "number" || typeof request.y !== "number") {
        throw new Error("Click relay input requires x and y.");
      }
      await session.page.mouse.click(request.x, request.y);
    } else if (request.action === "type") {
      if (typeof request.text !== "string" || request.text.length > 500) {
        throw new Error("Type relay input requires bounded text.");
      }
      await session.page.keyboard.type(request.text);
    } else if (request.action === "key") {
      if (typeof request.key !== "string" || request.key.length > 80) {
        throw new Error("Key relay input requires a bounded key.");
      }
      await session.page.keyboard.press(request.key);
    } else {
      throw new Error("Unsupported relay input.");
    }
    return { connectionId: request.connectionId, accepted: true, updatedAt: new Date().toISOString() };
  }

  async complete(connectionId: string): Promise<LoginRelayCompleteResult> {
    const session = this.requireLiveSession(connectionId);
    const state = (await session.context.storageState()) as BrowserStorageState;
    if (!hasLiepinAuthenticatedState(state)) {
      throw new LoginRelayNotVerifiedError();
    }
    if (session.scope !== undefined) {
      await this.sessionStore.writeStorageState(session.scope, state);
    }
    await this.close(connectionId);
    const result: LoginRelayCompleteResult = {
      connectionId,
      status: "ready",
      fixtureOnly: false,
    };
    if (session.scope !== undefined) {
      result.providerAccountHash = session.scope.providerAccountHash;
    }
    return result;
  }

  private requireLiveSession(connectionId: string): RelaySession {
    const session = this.sessions.get(connectionId);
    if (session === undefined) {
      throw new Error("Login relay session not found.");
    }
    if (session.expiresAt.getTime() <= Date.now()) {
      void this.close(connectionId);
      throw new Error("Login relay session expired.");
    }
    return session;
  }

  private async close(connectionId: string): Promise<void> {
    const session = this.sessions.get(connectionId);
    this.sessions.delete(connectionId);
    if (session !== undefined) {
      await session.context.close().catch(() => undefined);
      await session.browser.close().catch(() => undefined);
    }
  }
}

export function hasLiepinAuthenticatedState(state: BrowserStorageState): boolean {
  return (state.cookies ?? []).some((cookie) => isLiepinSessionCookie(cookie));
}

function isLiepinSessionCookie(cookie: Record<string, unknown>): boolean {
  if (typeof cookie.name !== "string" || typeof cookie.value !== "string" || typeof cookie.domain !== "string") {
    return false;
  }
  if (cookie.value.length === 0 || !isLiepinCookieDomain(cookie.domain)) {
    return false;
  }
  return isSessionLikeCookieName(cookie.name);
}

function isLiepinCookieDomain(domain: string): boolean {
  const normalized = domain.toLowerCase().replace(/^\./, "");
  return normalized === "liepin.com" || normalized.endsWith(".liepin.com");
}

function isSessionLikeCookieName(name: string): boolean {
  const normalized = name.toLowerCase();
  return ["auth", "login", "token", "session", "sid", "sso"].some((marker) => normalized.includes(marker));
}

async function safePageTitle(page: Page): Promise<string> {
  try {
    return (await page.title()).slice(0, 120);
  } catch {
    return "";
  }
}

function safePageOrigin(rawUrl: string): string {
  try {
    const parsed = new URL(rawUrl);
    return parsed.origin;
  } catch {
    return "about:blank";
  }
}
