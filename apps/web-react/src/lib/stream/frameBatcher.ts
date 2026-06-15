export function createFrameBatcher<T>(apply: (items: T[]) => void) {
  let queue: T[] = [];
  let frame: number | null = null;

  const flush = () => {
    frame = null;
    const items = queue;
    queue = [];
    if (items.length > 0) {
      apply(items);
    }
  };

  return {
    push(item: T) {
      queue.push(item);
      if (frame === null) {
        frame = requestAnimationFrame(flush);
      }
    },
    cancel() {
      if (frame !== null) {
        cancelAnimationFrame(frame);
      }
      frame = null;
      queue = [];
    },
  };
}
