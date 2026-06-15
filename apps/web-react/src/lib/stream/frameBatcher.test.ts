import { afterEach, describe, expect, it, vi } from "vitest";
import { createFrameBatcher } from "./frameBatcher";

describe("frame batcher", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("flushes queued items in one requestAnimationFrame callback", () => {
    expect.hasAssertions();

    let flush: FrameRequestCallback | undefined;
    const apply = vi.fn();
    const requestAnimationFrame = vi.fn((callback: FrameRequestCallback) => {
      flush = callback;
      return 7;
    });
    vi.stubGlobal("requestAnimationFrame", requestAnimationFrame);
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    const batcher = createFrameBatcher<number>(apply);
    batcher.push(1);
    batcher.push(2);

    expect(requestAnimationFrame).toHaveBeenCalledOnce();
    expect(apply).not.toHaveBeenCalled();

    if (flush === undefined) {
      throw new Error("requestAnimationFrame was not scheduled.");
    }
    flush(16);

    expect(apply).toHaveBeenCalledWith([1, 2]);
  });

  it("cancels the scheduled frame and drops queued items", () => {
    expect.hasAssertions();

    const apply = vi.fn();
    const cancelAnimationFrame = vi.fn();
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn(() => 11),
    );
    vi.stubGlobal("cancelAnimationFrame", cancelAnimationFrame);

    const batcher = createFrameBatcher<string>(apply);
    batcher.push("pending");
    batcher.cancel();

    expect(cancelAnimationFrame).toHaveBeenCalledWith(11);
    expect(apply).not.toHaveBeenCalled();
  });
});
