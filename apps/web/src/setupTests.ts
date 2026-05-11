import '@testing-library/jest-dom/vitest';

class ResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    const width = target instanceof HTMLElement ? target.offsetWidth : 0;
    const height = target instanceof HTMLElement ? target.offsetHeight : 0;
    const rect = {
      x: 0,
      y: 0,
      width,
      height,
      top: 0,
      right: width,
      bottom: height,
      left: 0,
      toJSON: () => ({}),
    } as DOMRectReadOnly;

    this.callback(
      [
        {
          target,
          contentRect: rect,
          borderBoxSize: [{ inlineSize: width, blockSize: height }],
          contentBoxSize: [{ inlineSize: width, blockSize: height }],
          devicePixelContentBoxSize: [{ inlineSize: width, blockSize: height }],
        } as ResizeObserverEntry,
      ],
      this as unknown as globalThis.ResizeObserver,
    );
  }

  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserver as unknown as typeof globalThis.ResizeObserver;
globalThis.DOMMatrixReadOnly = class DOMMatrixReadOnly {
  m22 = 1;
} as unknown as typeof globalThis.DOMMatrixReadOnly;

Object.defineProperties(HTMLElement.prototype, {
  offsetHeight: { configurable: true, get() { return 100; } },
  offsetWidth: { configurable: true, get() { return 180; } },
});

Object.defineProperty(SVGElement.prototype, 'getBBox', {
  configurable: true,
  value: () =>
    ({
      x: 0,
      y: 0,
      width: 0,
      height: 0,
    }) as DOMRect,
});
