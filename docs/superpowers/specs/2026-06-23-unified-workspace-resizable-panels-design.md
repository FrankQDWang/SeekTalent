# Unified Workspace With Resizable Panels — Design Spec

> **Date:** 2026-06-23  
> **Branch:** `codex/unified-workspace-resizable-panels`  
> **Base:** `codex/conversation-agent-controlled-orchestration` (476288f2)  
> **Status:** draft

---

## 1. Problem

当前首屏表单和对话工作区是两条独立路由。表单提交后用户被硬导航到新页面，没有过渡。workspace 内部面板宽度固定（chat 386px / graph 1fr），用户无法拖拽调整。文本排版高度在面板宽度变化时依赖浏览器 reflow。

## 2. Goals

1. **同页面 unified workspace：** 表单提交后在同一页面内收缩过渡，策略图自然展开
2. **可拖拽分隔线：** chat 和 graph 之间用 `PanelResizeHandle` 调整宽度，CSS 排版即时跟随
3. **Pretext 高性能文本预测量：** 拖拽时用 `@chenglou/pretext` 的 `layout()` 纯算术计算文本高度，消除 DOM reflow 抖动

## 3. Non-goals

- 不在 rail 和 chat 之间、graph 和 side 之间添加分隔线（仅 chat↔graph 一对）
- 不改变 right rail 的结构和行为
- 不改变 responsive compact workspace 的 tab 模式（≤1080px 时仍用 tab 切换）
- 不修改 BFF 契约和后端代码
- 不改变 storybook 的 StrategyGraph 和 HomeStartPanel 独立 story（它们各自保持独立）

## 4. Architecture

### 4.1 Route Merge

**Before（两条路由）：**

```
/                            → HomeStartPanel (全屏居中)
/conversations/$id           → ConversationScreen (chat + graph)
```

**After（单路由 + 视图阶段）：**

```
/                                → redirect to /conversations/new
/conversations/$conversationId  → UnifiedWorkbench
  stage: "home"       → HomeStartPanel (居中表单)
  stage: "transition" → 表单收缩动画 (~500ms)
  stage: "workspace"  → ResizablePanels (chat |↕drag| graph)
```

路由统一为 `/conversations/$conversationId`。参数为特殊值 `"new"` 时表示新建模式，stage 为 `"home"`。提交成功后，`conversationId` 从 `"new"` 更新为真实 ID，由于是同一 route pattern 的参数变化，React 组件保持挂载，`stage` 状态自然过渡。

- `/` 路由 301 redirect 到 `/conversations/new`
- 提交成功后 `navigate({ to: "/conversations/$conversationId", params: { conversationId: result.conversationId }, replace: true })`

### 4.2 View Stage State Machine

```
home ──(submit success)──→ transition ──(animation end)──→ workspace
```

- `"home"`：渲染 `HomeStartPanel`，占满整个 `main` 区域
- `"transition"`：`HomeStartPanel` 添加 CSS class `home-start-panel--collapsing`，graph 区域从 `opacity: 0` → `opacity: 1`。500ms 后自动进入 `"workspace"`
- `"workspace"`：`HomeStartPanel` 不再渲染，显示 `ResizableChatGraphLayout`

**状态由 React 组件本地管理**，不写入 URL query params。

### 4.3 Component Tree

```
ConversationShell
├── ConversationList (rail, 不变)
├── UnifiedWorkbench (main)
│   ├── [stage="home"] HomeStartPanel
│   ├── [stage="transition"] HomeStartPanel + StrategyGraph (fade in)
│   └── [stage="workspace"] ResizableChatGraphLayout
│       ├── Panel (chat, defaultSize=35, minSize=280px, maxSize=50%)
│       │   ├── Transcript
│       │   ├── RequirementReviewPanel
│       │   └── MessageComposer
│       ├── PanelResizeHandle (4px, cursor: col-resize)
│       └── Panel (graph, defaultSize=65, minSize=400px)
│           ├── StrategyGraph
│           ├── ThinkingProcessRail (candidates)
│           └── FinalReviewPanel
└── side rail (不变)
```

## 5. Detailed Design

### 5.1 Resizable Panel Layout

**Library:** `react-resizable-panels` (Brian Vaughn)

```tsx
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

function ResizableChatGraphLayout({ children }) {
  return (
    <PanelGroup direction="horizontal" autoSaveId="chat-graph-layout">
      <Panel defaultSize={35} minSizePixels={280} maxSize={50}>
        {/* chat: Transcript + MessageComposer */}
      </Panel>
      <PanelResizeHandle className="workspace-resize-handle" />
      <Panel defaultSize={65} minSizePixels={400}>
        {/* graph: StrategyGraph + tabs */}
      </Panel>
    </PanelGroup>
  );
}
```

**PanelResizeHandle 样式：**

```css
.workspace-resize-handle {
  background: transparent;
  width: 4px;
  transition: background 150ms;
}

.workspace-resize-handle:hover,
.workspace-resize-handle[data-resize-handle-active] {
  background: var(--st-action);
}
```

- `data-resize-handle-active` 由 `react-resizable-panels` 在拖拽时自动设置
- 不渲染可视 grip 图标（简洁），hover 高亮即可
- 键盘可访问：`react-resizable-panels` 内置键盘 resize 支持

**CSS Grid 移除：**

`ConversationScreen.css` 中：
```css
/* 移除 */
.conversation-view__workspace[data-workflow-surface="visible"] {
  grid-template-columns: 386px minmax(0, 1fr);
}

/* 改为 */
.conversation-view__workspace[data-workflow-surface="visible"] {
  display: flex;
  flex-direction: column;
}
```

`PanelGroup` 自行管理内部两列布局。

### 5.2 HomeStartPanel Collapse Animation

```css
.home-start-panel--collapsing {
  animation: home-panel-collapse 500ms ease forwards;
}

@keyframes home-panel-collapse {
  0% {
    opacity: 1;
    transform: scale(1);
  }
  100% {
    opacity: 0;
    transform: scale(0.95);
  }
}

@media (prefers-reduced-motion: reduce) {
  .home-start-panel--collapsing {
    animation: none;
    opacity: 0;
  }
}
```

**StrategyGraph 淡入：**

```css
.strategy-graph--entering {
  animation: graph-fade-in 500ms ease 150ms forwards;
  opacity: 0;
}

@keyframes graph-fade-in {
  to { opacity: 1; }
}
```

**实现：** 在 `transition` 阶段，`HomeStartPanel` 挂载 `--collapsing` class，同时 `ResizableChatGraphLayout` 用 `--entering` class。`onAnimationEnd` 或 `setTimeout(500)` 切换到 `"workspace"`。

### 5.3 Pretext Integration

**Library:** `@chenglou/pretext`

**用途：** Transcript 消息的文本高度预测量。拖拽面板宽度变化时，在 DOM 更新前就算出文本在新宽度下的高度。

**集成点：** `Transcript.tsx` 中的消息渲染

```tsx
import { prepare, layout } from "@chenglou/pretext";

// 消息到达时
const prepared = prepare(messageText, "14px system-ui, sans-serif");

// 在 Panel 的 onResize 回调中
function onResize(size: number) {
  const panelWidth = size * containerWidth / 100;
  const { height, lineCount } = layout(prepared, panelWidth, 20);
  // 用 height 设置消息 min-height，避免 reflow
}
```

**Pretext 不替代 CSS 渲染。** CSS 仍负责实际画文字。Pretext 只做预测量——在 DOM 还没更新之前就知道文本在新宽度下占多高，从而预先分配空间。

**性能预期：**
- `prepare()`：~5ms，消息到达时执行一次，缓存结果
- `layout()`：~0.01ms，拖拽每帧执行，纯算术
- 对比传统方式（每次 `getBoundingClientRect()`）：10-100ms reflow

**集成策略：**
- 只对长文本消息（>200 字符）启用 Pretext 预测量
- 短消息直接用 CSS `line-clamp` 或原样渲染
- `prepared` 缓存以 `messageId` 为 key，存在 `useRef` 或 `useMemo` 中

**Pretext 配置：**

```ts
const font = "14px system-ui, -apple-system, sans-serif";
const options = {
  whiteSpace: "normal" as const,
  wordBreak: "normal" as const,
};
```

### 5.4 Route Changes

#### `routes/root.tsx`

```tsx
// 旧: 两个独立路由
export const indexRoute = createRoute({ path: "/", component: WorkbenchIndexRoute });
export const conversationRoute = createRoute({
  path: "/conversations/$conversationId",
  component: ConversationRoute,
});

// 新: 单路由，/ 路由 redirect
export const indexRoute = createRoute({
  path: "/",
  component: () => {
    const navigate = useNavigate();
    useEffect(() => { navigate({ to: "/conversations/new", replace: true }); }, []);
    return null;
  },
});
export const conversationRoute = createRoute({
  path: "/conversations/$conversationId",
  component: WorkbenchRoute,
});
```

通过 `conversationId` 参数值区分阶段：

- `params.conversationId === "new"` → 新建模式，stage = `"home"`
- `params.conversationId` 为真实 ID → 加载已有会话，stage = `"workspace"`（如果数据已加载）

#### 提交逻辑

```tsx
const onSubmit = async (input: HomeStartPanelSubmitInput) => {
  const result = await createConversationMutation.mutateAsync(input);
  setStage("transition");
  // 动画结束后
  setTimeout(() => setStage("workspace"), 500);
  // 更新 URL 为真实 conversationId（同一 route pattern，组件不卸载）
  navigate({
    params: { conversationId: result.conversationId },
    to: "/conversations/$conversationId",
    replace: true,
  });
};
```

### 5.5 Files Changed

| File | Change |
|------|--------|
| `routes/root.tsx` | 路由合并：`/` redirect 到 `/conversations/new`，`WorkbenchRoute` 统一入口 |
| `routes/conversation.tsx` | 保留现有业务逻辑（message submit、requirement mutations 等），在 `WorkbenchRoute` 中按 stage 条件渲染 |
| `components/workbench/ConversationShell.tsx` | 透传 `stage` 状态 |
| `components/workbench/ConversationScreen.tsx` | 用 `PanelGroup` 替代 CSS Grid；`home`/`transition` stage 时渲染 `HomeStartPanel` |
| `components/workbench/ConversationScreen.css` | 移除 `grid-template-columns`，添加 resize handle 样式和 collapse 动画 |
| `components/workbench/HomeStartPanel.tsx` | 添加 `--collapsing` 动画 class prop |
| `components/workbench/HomeStartPanel.css` | 添加 collapse keyframes |
| `components/workbench/Transcript.tsx` | 集成 Pretext `prepare`/`layout` |
| `components/workbench/ResizableChatGraphLayout.tsx` | **新增**，封装 PanelGroup + Panel + PanelResizeHandle |
| `components/workbench/ResizableChatGraphLayout.css` | **新增** |
| `package.json` | 新增 `@chenglou/pretext` + `react-resizable-panels` |

**注意：** `routes/conversation.tsx` 中的 `ConversationRoute` 组件逻辑（useAgentWorkbenchLiveConversation、message submit、requirement mutations 等）整体保留，迁移到 `WorkbenchRoute` 中按 stage 条件执行。`"new"` conversationId 时跳过 BFF 查询，等待用户提交表单。

## 6. Edge Cases and States

### 6.1 Loading

- `stage="home"` 时 `HomeStartPanel` 的 loading 状态不变
- `stage="transition"` 期间 graph 区域显示骨架或 `等待检索策略生成` 空状态
- `stage="workspace"` 时，BFF 数据未到达则保持现有空状态逻辑

### 6.2 Error

- 表单提交失败：与现有行为一致，显示错误信息，不进入 transition
- BFF 加载失败：与现有 `ConversationScreen` 错误处理一致
- 会话不存在：与现有路由行为一致，显示错误状态

### 6.3 Empty

- 新建会话（无策略图数据）：`hasConversationWorkflowSurface()` 返回 false，graph 面板显示空状态。Panel 仍可渲染，但内容为空
- **`/conversations/new` 直接访问：** `conversationId === "new"` 时，跳过 `useAgentWorkbenchLiveConversation` 请求和所有 BFF 查询。ConversationRoute 的 BFF 逻辑只在 `conversationId !== "new"` 时执行。`HomeStartPanel` 的 `onSubmit` 中创建真实会话后，用 `navigate({ params: { conversationId: result.conversationId }, replace: true })` 更新参数，此时 `useConversationId` 变为真实 ID，BFF query 自动触发

### 6.4 Panel Resize Boundaries

- **Chat 最小宽度：** 280px（`minSizePixels={280}`），防止 transcript 消息被压碎
- **Chat 最大宽度：** 50%（`maxSize={50}`），防止 graph 被挤到不可用
- **Graph 最小宽度：** 400px（`minSizePixels={400}`），确保策略图节点完整可见
- 策略图的 `transform: scale()` auto-fit 逻辑保持不变，在 Panel 宽度变化时自动适配

### 6.5 Responsive (≤1080px)

- `useCompactWorkspace()` 仍触发 tab 模式
- 此时 `PanelGroup` 不渲染，回退到现有 tab 切换逻辑
- 分隔线在 compact 模式下不渲染

### 6.6 prefers-reduced-motion

- collapse 动画和 graph fade-in 全部跳过
- `PanelResizeHandle` 拖拽仍可用（这不是动画，是用户交互）

### 6.7 Keyboard

- `PanelResizeHandle` 内置键盘支持：`Home/End` 跳到 min/max，`ArrowLeft/Right` 微调
- 表单提交、tab 切换的键盘访问保持不变

## 7. Acceptance Criteria

1. 表单提交后，表单平滑收缩消失（500ms），策略图淡入展开
2. chat 和 graph 之间有可拖拽分隔线，hover 变色
3. 拖拽时 transcript 文本高度即时跟随，无可见抖动
4. 面板宽度在 localStorage 持久化，刷新后保持
5. `prefers-reduced-motion` 时跳过动画
6. ≤1080px 时回退到 tab 模式，分隔线不渲染
7. 现有 28 个测试文件/101 个测试全部通过，无回归
8. 新增测试覆盖：Panel 边界、collapse 动画、Pretext 集成

## 8. Dependencies

- `react-resizable-panels` — 面板拖拽
- `@chenglou/pretext` — 文本预测量

## 9. Risks

- **Pretext 字体匹配：** `prepare()` 的 font string 必须与 CSS 渲染字体完全一致，否则测量不准。需要验证 `system-ui, -apple-system, sans-serif` 在 Canvas 和 DOM 中的表现一致
- **PanelGroup 与现有 CSS 冲突：** `PanelGroup` 内部使用 CSS flexbox，需要确保与 `ConversationScreen` 现有的 flex 布局不冲突
- **路由变更：** 合并路由可能影响已有的 URL 导航逻辑，需要全面测试

## 10. Test Plan

### Unit/Integration

- `ResizableChatGraphLayout.test.tsx`：Panel 渲染、min/max 边界、resize handle 存在
- `HomeStartPanel.test.tsx`（扩展）：collapse animation class 添加、transition 阶段状态
- `Transcript.test.tsx`（扩展）：Pretext 集成、prepare 调用、layout 调用

### Storybook

- `WorkbenchShell/ResizableLayout`：展示可拖拽的 chat↔graph 布局
- `HomeStartPanel/Collapsing`：展示 collapse 动画

### Playwright Visual

- `workbench-resizable-chat-graph`：可拖拽面板截图
- `workbench-home-collapse`：collapse 动画截图