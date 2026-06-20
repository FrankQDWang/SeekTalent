import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { AgentStrategyGraph } from "../../lib/strategy-graph/graphProjection";
import { StrategyGraph } from "./StrategyGraph";

class TestResizeObserver implements ResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    const isNode =
      target instanceof HTMLElement &&
      target.classList.contains("react-flow__node");
    const contentRect = DOMRectReadOnly.fromRect({
      height: isNode ? 96 : 560,
      width: isNode ? 232 : 900,
    });

    this.callback([{ contentRect, target } as ResizeObserverEntry], this);
  }

  unobserve() {}

  disconnect() {}
}

class TestDOMMatrixReadOnly {
  readonly m22: number;

  constructor(transform?: string) {
    const matrixMatch = transform?.match(/matrix\(([^)]+)\)/);
    if (matrixMatch?.[1]) {
      const values = matrixMatch[1]
        .split(",")
        .map((value) => Number(value.trim()));
      const zoom = values.at(3);
      this.m22 = typeof zoom === "number" && Number.isFinite(zoom) ? zoom : 1;
      return;
    }

    const scaleMatch = transform?.match(/scale\(([^)]+)\)/);
    this.m22 = scaleMatch?.[1] ? Number(scaleMatch[1]) : 1;
  }
}

let offsetWidthSpy: { mockRestore: () => void };
let offsetHeightSpy: { mockRestore: () => void };
type SVGElementWithBBox = SVGElement & {
  getBBox?: () => { height: number; width: number; x: number; y: number };
};

const svgPrototype = SVGElement.prototype as SVGElementWithBBox;
const originalGetBBox = svgPrototype.getBBox;

const graph: AgentStrategyGraph = {
  nodes: [
    {
      nodeId: "requirements",
      kind: "requirements",
      label: "需求拆解",
      summary: "已确认岗位要求，准备启动多轮检索。",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "round_1_query",
      kind: "activity",
      label: "第 1 轮 · 查询包",
      summary: "第 1 轮查询策略已生成",
      status: "running",
      sourceKind: "liepin",
    },
  ],
  edges: [
    {
      edgeId: "requirements->round_1_query",
      fromNodeId: "requirements",
      toNodeId: "round_1_query",
      label: "生成检索策略",
    },
  ],
};

describe("StrategyGraph", () => {
  beforeAll(() => {
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    vi.stubGlobal("DOMMatrixReadOnly", TestDOMMatrixReadOnly);
    vi.stubGlobal("DOMMatrix", TestDOMMatrixReadOnly);
    offsetWidthSpy = vi
      .spyOn(HTMLElement.prototype, "offsetWidth", "get")
      .mockImplementation(function (this: HTMLElement) {
        return this.classList.contains("react-flow__node") ? 232 : 900;
      });
    offsetHeightSpy = vi
      .spyOn(HTMLElement.prototype, "offsetHeight", "get")
      .mockImplementation(function (this: HTMLElement) {
        return this.classList.contains("react-flow__node") ? 96 : 560;
      });
    Object.defineProperty(svgPrototype, "getBBox", {
      configurable: true,
      value: () => ({ height: 20, width: 96, x: 0, y: 0 }),
    });
  });

  afterAll(() => {
    offsetWidthSpy.mockRestore();
    offsetHeightSpy.mockRestore();
    if (originalGetBBox) {
      Object.defineProperty(svgPrototype, "getBBox", {
        configurable: true,
        value: originalGetBBox,
      });
    } else {
      Reflect.deleteProperty(svgPrototype, "getBBox");
    }
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders a nonblank accessible read-only React Flow graph", async () => {
    expect.hasAssertions();

    const { container } = render(<StrategyGraph graph={graph} />);

    const region = screen.getByRole("region", { name: "检索策略图" });
    expect(region).toBeVisible();
    expect(await screen.findByText("需求拆解")).toBeVisible();
    expect(screen.getByText("第 1 轮 · 查询包")).toBeVisible();
    expect(container.querySelector(".react-flow")).toBeInTheDocument();
    expect(screen.queryByLabelText("检索策略图控制")).not.toBeInTheDocument();
  });

  it("does not select nodes or open detail UI when clicked", async () => {
    expect.hasAssertions();

    render(<StrategyGraph graph={graph} />);

    const region = screen.getByRole("region", { name: "检索策略图" });
    await within(region).findByText("第 1 轮 · 查询包");
    const node = screen.getByTestId("strategy-node-round_1_query");

    fireEvent.click(node);

    expect(node).toHaveAttribute("data-selected", "false");
    expect(
      screen.queryByRole("complementary", { name: /节点详情/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: /节点详情/ }),
    ).not.toBeInTheDocument();
  });
});
