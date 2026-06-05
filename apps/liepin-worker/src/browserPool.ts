import { chromium, type BrowserContextOptions } from "playwright";

export type WorkerPage = {
  goto(url: string, options?: { waitUntil?: "domcontentloaded" | "load" | "networkidle" }): Promise<unknown>;
  content?: () => Promise<string>;
};

type WorkerBrowserContext = {
  newPage(): Promise<WorkerPage>;
  close(): Promise<void>;
};

type WorkerBrowser = {
  newContext(options: BrowserContextOptions): Promise<WorkerBrowserContext>;
  close(): Promise<void>;
  isConnected?: () => boolean;
};

export type WorkerBrowserLauncher = {
  launch(options: { headless: boolean }): Promise<WorkerBrowser>;
};

export type ManagedBrowserPool = {
  withPage<T>(options: {
    storageState: unknown;
    run: (page: WorkerPage) => Promise<T>;
  }): Promise<T>;
  cleanupIdle(): Promise<void>;
  shutdown(): Promise<void>;
};

type ManagedBrowserPoolOptions = {
  launcher?: WorkerBrowserLauncher;
  idleTtlMs?: number;
  now?: () => number;
};

const DEFAULT_IDLE_TTL_MS = 5 * 60 * 1000;

export function createManagedBrowserPool(options: ManagedBrowserPoolOptions = {}): ManagedBrowserPool {
  return new DefaultManagedBrowserPool(options);
}

class DefaultManagedBrowserPool implements ManagedBrowserPool {
  private browser: WorkerBrowser | undefined;
  private lastUsedAt: number;

  constructor(private readonly options: ManagedBrowserPoolOptions) {
    this.lastUsedAt = this.now();
  }

  async withPage<T>(options: {
    storageState: unknown;
    run: (page: WorkerPage) => Promise<T>;
  }): Promise<T> {
    const context = await this.openContextWithRetry(options.storageState);
    try {
      const page = await context.newPage();
      return await options.run(page);
    } finally {
      await context.close();
      this.lastUsedAt = this.now();
      await this.cleanupIdle();
    }
  }

  async cleanupIdle(): Promise<void> {
    const browser = this.browser;
    if (browser === undefined) {
      return;
    }
    const ttlMs = this.options.idleTtlMs ?? DEFAULT_IDLE_TTL_MS;
    if (this.now() - this.lastUsedAt >= ttlMs) {
      await this.closeBrowser();
    }
  }

  async shutdown(): Promise<void> {
    await this.closeBrowser();
  }

  private async openContextWithRetry(
    storageState: unknown,
  ): Promise<WorkerBrowserContext> {
    const contextOptions: BrowserContextOptions = {
      storageState: storageState as NonNullable<BrowserContextOptions["storageState"]>,
    };
    try {
      return await (await this.browserForUse()).newContext(contextOptions);
    } catch {
      await this.closeBrowser();
      return await (await this.browserForUse()).newContext(contextOptions);
    }
  }

  private async browserForUse(): Promise<WorkerBrowser> {
    if (this.browser === undefined || this.browser.isConnected?.() === false) {
      await this.closeBrowser();
      this.browser = await this.launcher().launch({ headless: true });
    }
    return this.browser;
  }

  private async closeBrowser(): Promise<void> {
    const browser = this.browser;
    this.browser = undefined;
    if (browser !== undefined) {
      await browser.close();
    }
  }

  private launcher(): WorkerBrowserLauncher {
    return this.options.launcher ?? chromium;
  }

  private now(): number {
    return this.options.now?.() ?? Date.now();
  }
}
