# SeekTalent 前端/BFF/后端分层现状与解耦Gap分析报告

## 执行摘要

SeekTalent 当前采用了**不完全的三层分离**架构：
- **第一层（Frontend）**: Svelte SPA（`/apps/web-svelte`）- 相对独立
- **第二层（API/BFF）**: FastAPI 路由与响应转换（`/src/seektalent_ui`）- 部分 BFF，但混合了业务逻辑
- **第三层（Backend）**: 核心业务逻辑（`/src/seektalent` + `runtime`）- 与 API 紧耦合

**核心问题**：API 层直接使用后端内部模型进行转换，而非通过清晰的 BFF 契约解耦。

---

## 1. 前端代码位置与组织

### 位置与技术栈
- **物理位置**: `/Users/frankqdwang/Agents/SeekTalent-0.2.4/apps/web-svelte`
- **项目类型**: 独立的 SvelteKit + Vite 项目（完全独立仓库结构）
- **技术栈**: 
  - Svelte 5.55.2（reactive runes 模式）
  - SvelteKit 2.57.0（静态适配器）
  - TailwindCSS 4.2.2
  - @tanstack/svelte-query 6.1.29（数据查询）
  - @xyflow/svelte 1.5.2（图形渲染）
  - openapi-fetch 0.17.0（类型安全 API 调用）

### 前端代码结构
```
/apps/web-svelte/src/
├── lib/
│   ├── api/
│   │   ├── client.ts          # 通用 API 客户端（CSRF、错误处理）
│   │   ├── workbench.ts       # 工作台 API 调用集合
│   │   ├── schema.d.ts        # OpenAPI 生成的类型定义（95KB）
│   │   └── errors.ts
│   ├── workbench/             # 工作台业务逻辑
│   │   ├── eventStream.ts     # SSE/EventSource 管理
│   │   ├── runtimeGraphView.ts
│   │   ├── types.ts           # 前端业务类型
│   │   └── ...
│   ├── components/            # UI 组件（39 个）
│   ├── query/                 # 查询键管理
│   └── assets/
├── routes/
│   ├── (app)/                 # 应用主路由
│   ├── (auth)/                # 认证相关路由
│   └── +layout.svelte
└── test/                      # Vitest 单元测试
```

### 前端独立性评估
✓ **完全独立的构建流程**: `npm run build` 生成静态资源
✓ **无 Python 依赖**: 纯 Node.js/TypeScript 栈
✓ **无直接代码耦合**: 不导入任何 `seektalent` 模块
✓ **API-first 设计**: 所有数据通过 HTTP API（openapi-fetch）获取
✗ **API 类型耦合**: schema.d.ts 直接从后端 OpenAPI 生成（紧耦合）

---

## 2. 当前 API 层（现状的"BFF"）

### API 层位置与结构
- **后端 API 主文件**: `/src/seektalent_ui/server.py`（FastAPI 应用工厂）
- **路由定义**:
  - `/src/seektalent_ui/workbench_routes.py`（826 行）
  - `/src/seektalent_ui/event_routes.py`（294 行）
  - `/src/seektalent_ui/workbench_source_connection_routes.py`（268 行）
  - `/src/seektalent_ui/workbench_auth_routes.py`（146 行）
- **数据模型**: `/src/seektalent_ui/models.py`（908 行 - 纯响应 DTO）
- **数据转换逻辑**:
  - `/src/seektalent_ui/workbench_response.py`（501 行）
  - `/src/seektalent_ui/resume_snapshot_projection.py`（543 行）
  - `/src/seektalent_ui/runtime_graph.py`（776 行）
  - `/src/seektalent_ui/job_runner.py`（448 行）

### API 端点清单（总计 ~45+ 个端点）

#### 认证端点
```
POST   /api/auth/bootstrap              # 引导管理员用户
GET    /api/auth/me                     # 获取当前用户
POST   /api/auth/login                  # 登录
POST   /api/auth/logout                 # 登出
```

#### 会话管理
```
GET    /api/workbench/sessions                       # 列表
POST   /api/workbench/sessions                       # 创建
GET    /api/workbench/sessions/{session_id}          # 详情
POST   /api/workbench/sessions/{session_id}/start    # 启动源运行
GET    /api/workbench/sessions/{session_id}/runtime-graph    # 运行时图
```

#### 需求审查
```
GET    /api/workbench/sessions/{session_id}/requirements
PUT    /api/workbench/sessions/{session_id}/requirements
POST   /api/workbench/sessions/{session_id}/requirements/prepare
POST   /api/workbench/sessions/{session_id}/requirements/approve
```

#### 候选人审查
```
GET    /api/workbench/sessions/{session_id}/candidates
PUT    /api/workbench/sessions/{session_id}/candidates/{review_item_id}
POST   /api/workbench/sessions/{session_id}/candidates/{review_item_id}/provider-actions/open
GET    /api/workbench/sessions/{session_id}/final-top10
```

#### 候选人详情与图
```
GET    /api/workbench/sessions/{session_id}/graph-candidates     # 分页列表
GET    /api/workbench/sessions/{session_id}/graph-candidates/{id}/resume-snapshot
POST   /api/workbench/sessions/{session_id}/candidates/{id}/detail-open-requests
```

#### 详情开放请求
```
GET    /api/workbench/detail-open-requests
POST   /api/workbench/detail-open-requests/{request_id}/approve
POST   /api/workbench/detail-open-requests/{request_id}/reject
```

#### 源连接（Liepin 集成）
```
GET    /api/workbench/source-connections
POST   /api/workbench/source-connections/liepin
GET    /api/workbench/source-connections/{connection_id}
GET    /api/workbench/source-connections/{connection_id}/login/frame
GET    /api/workbench/source-connections/{connection_id}/login/snapshot
POST   /api/workbench/source-connections/{connection_id}/login/input
```

#### 源运行策略
```
GET    /api/workbench/sessions/{session_id}/source-runs/liepin/policy
PUT    /api/workbench/sessions/{session_id}/source-runs/liepin/policy
```

#### 事件流（SSE + 轮询）
```
GET    /api/workbench/events                                    # 轮询
GET    /api/workbench/sessions/{session_id}/events              # 轮询
GET    /api/workbench/events/stream                             # SSE
GET    /api/workbench/sessions/{session_id}/events/stream       # SSE
```

#### 管理与设置
```
GET    /api/workbench/dev-mode/status
GET    /api/workbench/security-audit-events
GET    /api/workbench/settings
```

#### Liepin 独立 API（非 Workbench）
```
POST   /api/liepin/compliance-gates
GET    /api/liepin/compliance-gates/{gate_ref}
POST   /api/liepin/compliance-gates/{gate_ref}/bind-account
POST   /api/liepin/compliance-gates/{gate_ref}/verify
POST   /api/liepin/connections
GET    /api/liepin/connections/{connection_id}
POST   /api/liepin/connections/{connection_id}/login-url
POST   /api/liepin/connections/{connection_id}/stream-token
GET    /api/liepin/connections/{connection_id}/events          # SSE
```

### 数据格式示例

#### 请求格式（JSON + Pydantic）
```python
# 创建会话
WorkbenchSessionCreateRequest:
  jobTitle: str
  jd: str
  notes: str = ""

# 更新候选人评审项
WorkbenchCandidateReviewItemUpdateRequest:
  status: "reviewed" | "flagged"
  reviewerNote: str | None
  dispositionReason: str | None
```

#### 响应格式（JSON）
```python
# 会话响应
WorkbenchSessionResponse:
  sessionId: str
  jobTitle: str
  jd: str
  notes: str
  status: SessionStatus
  sourceRuns: list[WorkbenchSourceRunResponse]
  sourceCards: list[WorkbenchSourceCardResponse]
  ...

# 候选人审查项
WorkbenchCandidateReviewItemResponse:
  reviewItemId: str
  candidateName: str
  summary: str
  strengths: list[str]
  weaknesses: list[str]
  dispositionReason: str | None
  evidenceSnapshot: WorkbenchCandidateEvidenceResponse
  ...

# 简历快照
WorkbenchGraphCandidateResumeSnapshotResponse:
  graphCandidateId: str
  status: "snapshot_found" | "snapshot_not_found" | "snapshot_forbidden"
  sourceCompleteness: "complete" | "partial" | "unavailable"
  profile: WorkbenchResumeSnapshotProfileResponse | None
  workExperience: list[WorkbenchResumeSnapshotWorkExperienceResponse]
  education: list[WorkbenchResumeSnapshotEducationResponse]
  ...
```

### API 响应规模
- **会话列表**: 通常 5-10 个会话（较小）
- **候选人列表**: 100-300 个候选人，每个包含完整证据（中等）
- **图候选人**: 按 node_id 分页，limit=25（优化）
- **简历快照**: 包含完整工作经历/教育/项目信息（大）
- **运行时图**: 可包含 100+ 节点的 DAG 结构（很大）

---

## 3. 数据流：Backend 模型 → API 响应 → Frontend 展示

### 完整数据路径示例：获取会话候选人列表

```
┌─────────────────────────────────────────────────────────────────┐
│ Frontend (Svelte)                                              │
│ /api/workbench.ts                                              │
│  listCandidateReviewItems(sessionId) ─────────────────────┐   │
└────────────────────────────────────────────────────────────│───┘
                                                             │
                                                    HTTP GET │
                                                             ↓
┌─────────────────────────────────────────────────────────────────┐
│ Backend API Layer (seektalent_ui)                           │
│                                                             │
│ workbench_routes.py:                                        │
│  @router.get(.../candidates)                               │
│  def list_candidate_review_items(...)                       │
│    → WorkbenchCandidateReviewQueueResponse                 │
│                                                             │
│ workbench_response.py:                                      │
│  candidate_review_item_response(                            │
│    item: WorkbenchCandidateReviewItem,  ◄─┐               │
│    evidence: WorkbenchCandidateEvidence    │               │
│  ) → WorkbenchCandidateReviewItemResponse │               │
│                                           │               │
│ workbench_store.py:                       │               │
│  get_candidate_review_items()             │               │
│  ─────────────────────────────────────────┘               │
│    ↑                                                        │
└────┼────────────────────────────────────────────────────────┘
     │
     │ Query (SQLite)
     ↓
┌─────────────────────────────────────────────────────────────────┐
│ Backend Core Business Logic (seektalent)                    │
│                                                             │
│ models.py:                                                  │
│  ├─ WorkflowCandidateReview                               │
│  ├─ CandidateEvidence                                     │
│  ├─ FinalTopCandidate                                     │
│  └─ ... (业务模型，50+ 类)                                │
│                                                             │
│ runtime/:                                                   │
│  ├─ orchestrator.py (运行流程控制)                         │
│  ├─ scoring.py (评分逻辑)                                 │
│  ├─ reflection.py (反思逻辑)                              │
│  └─ ... (核心 AI 工作流)                                   │
│                                                             │
│ evaluation.py (评估与反思框架)                            │
│ corpus/ (检索存储)                                         │
│ artifacts/ (运行产物管理)                                  │
│ storage/ (持久化)                                          │
│                                                             │
└────────────────────────────────────────────────────────────────┘
```

### 关键转换点分析

#### 转换 1: 从后端内部模型到 BFF 响应
**位置**: `/src/seektalent_ui/workbench_response.py:73-150`

```python
def candidate_review_item_response(
    item: WorkbenchCandidateReviewItem,        # 存储层模型
    evidence: WorkbenchCandidateEvidence,      # 证据聚合
    ...
) -> WorkbenchCandidateReviewItemResponse:    # 响应 DTO
    return WorkbenchCandidateReviewItemResponse(
        reviewItemId=item.review_item_id,
        candidateName=item.candidate_name,
        summary=item.summary,
        strengths=item.strengths,
        weaknesses=item.weaknesses,
        dispositionReason=item.disposition_reason,
        evidenceSnapshot=_evidence_response(evidence),
        ...
    )
```

**问题**:
- 转换分散在多个 `*_response.py` 文件中
- 响应 DTO 与后端存储模型直接映射（无业务逻辑屏障）
- 前端可见后端实现细节

#### 转换 2: 简历快照投影（复杂转换）
**位置**: `/src/seektalent_ui/resume_snapshot_projection.py:28-75`

```python
def build_resume_snapshot_response(
    *,
    settings: AppSettings,
    graph_secret: str,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    graph_candidate_id: str,
) -> WorkbenchGraphCandidateResumeSnapshotResponse | None:
    # 从 WorkbenchStore 查询候选人
    candidate = resolve_graph_candidate(...)
    
    # 从 CorpusStore 读取简历文档
    corpus = CorpusStore(settings.corpus_path)
    docs = corpus.get_resume_documents_by_snapshot_sha256(...)
    
    # 投影（transform）文档到前端格式
    return _project_doc(
        graph_candidate_id=graph_candidate_id,
        corpus=corpus,
        doc=doc,
        fallback=candidate.summary
    )
```

**特点**: 混合了数据库查询、文件系统访问和格式转换

#### 转换 3: 运行时图构建（DAG 转换）
**位置**: `/src/seektalent_ui/runtime_graph.py:1-100`

```python
def build_runtime_graph(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
) -> WorkbenchRuntimeGraphResponse:
    # 从 WorkbenchStore 获取运行时状态
    runtime_state = store.get_runtime_state(...)
    
    # 构建节点和边
    nodes = [_node_response(node) for node in runtime_state.nodes]
    edges = [_edge_response(edge) for edge in runtime_state.edges]
    
    # 计算可视化布局（ELK 兼容）
    return WorkbenchRuntimeGraphResponse(
        nodes=nodes,
        edges=edges,
        layout=_compute_layout(nodes, edges)
    )
```

**特点**: 包含算法（布局计算）

### 前端消费模式

#### 模式 1: 直接 fetch + 类型检查
```typescript
// /apps/web-svelte/src/lib/api/workbench.ts:56-67
export async function getSession(sessionId: string) {
    const result = await api.GET('/api/workbench/sessions/{session_id}', {
        params: { path: { session_id: sessionId } }
    });
    if (result.data === undefined) {
        console.error('Workbench getSession failed', {
            error: result.error,
            status: result.response.status
        });
    }
    return requireData(result);
}
```

#### 模式 2: SSE 事件流
```typescript
// /apps/web-svelte/src/lib/workbench/eventStream.ts:64-94
const url = sessionId
    ? `/api/workbench/sessions/${encodeURIComponent(sessionId)}/events/stream`
    : '/api/workbench/events/stream';

source = new EventSource(url);
source.addEventListener('workbench_event', handleEvent);
source.addEventListener('message', handleEvent);
```

#### 模式 3: 分页查询
```typescript
// /apps/web-svelte/src/lib/api/workbench.ts:279-304
export async function listSessionEvents(sessionId: string, afterSeq = 0) {
    const events: WorkbenchEvent[] = [];
    let cursor = afterSeq;
    
    for (let pageIndex = 0; pageIndex < EVENT_MAX_PAGES; pageIndex += 1) {
        const page = requireData(
            await api.GET('/api/workbench/sessions/{session_id}/events', {
                params: {
                    path: { session_id: sessionId },
                    query: { after_seq: cursor, limit: EVENT_PAGE_LIMIT }
                }
            })
        );
        events.push(...page.events);
        if (page.events.length < EVENT_PAGE_LIMIT) break;
        cursor = page.events.at(-1)?.globalSeq ?? cursor;
    }
    
    return { events };
}
```

---

## 4. 前后端耦合点分析

### 耦合点 1: API 类型定义（最紧密）
**当前状态**: OpenAPI schema 直接生成前端类型
```
后端 DTO (models.py) 
    ↓
FastAPI OpenAPI 生成
    ↓
openapi-typescript 工具
    ↓
schema.d.ts (95KB)
    ↓
前端 TypeScript 中导入 `components['schemas']['...']`
```

**问题**:
- 后端任何 DTO 字段改动 → 前端 schema 变化 → 前端代码可能崩溃
- 无版本控制的 API 契约
- 无破坏性变更检测

### 耦合点 2: 业务逻辑分布
**问题**: 一些业务逻辑分散在三层：

1. **后端核心** (`/src/seektalent/runtime`): 候选人评分、反思、最终排名
2. **BFF 层** (`/src/seektalent_ui`): 状态投影、权限检查、审计日志
3. **前端** (`/apps/web-svelte/src/lib/workbench`): UI 状态管理、排序、过滤

例：候选人排序
- 后端计算初始顺序（`evaluation.py`)
- BFF 投影到响应中（`workbench_response.py`)
- 前端可本地重新排序（前端 query 状态）

### 耦合点 3: 数据库访问
**问题**: API 层直接访问 SQLite 数据库
```python
# workbench_routes.py:325
def list_session_graph_candidates(...):
    store = get_workbench_store(request)  # 直接 SQLite 访问
    candidates = store.list_graph_candidates(...)
```

- 无 GraphQL 或通用查询语言
- 无字段选择（always over-fetch）
- 难以版本化

### 耦合点 4: 共享配置
**问题**: 前端依赖后端配置常量
```python
# workbench_candidate_graph.py:67
DEFAULT_GRAPH_CANDIDATE_LIMIT = 25
MAX_GRAPH_CANDIDATE_LIMIT = 100
```

这些常量也应该从 API 响应的元数据中获取。

### 耦合点 5: EventSource/SSE 实现细节
**问题**: SSE 事件格式在前端硬编码
```typescript
// eventStream.ts:84
source.addEventListener('workbench_event', handleEvent);
source.addEventListener('message', handleEvent);
```

后端修改事件格式 → 前端监听失败

---

## 5. BFF 层现状评估

### 现有 BFF 特征
| 特征 | 现状 | 评分 |
|------|------|------|
| 响应 DTO 定义 | 明确的 Pydantic 模型（908 行） | ✓✓✓ |
| 路由组织 | 按功能域分文件（auth/workbench/events） | ✓✓ |
| 错误处理 | CORS + 验证异常处理 | ✓✓ |
| 认证/授权 | 基于 JWT Cookie + CSRF | ✓✓✓ |
| 数据转换 | 分散的 `*_response.py` 文件 | ✓ |
| API 版本管理 | 无版本控制 | ✗ |
| 文档 | OpenAPI/Swagger 自动生成 | ✓✓ |
| 缓存策略 | 无 | ✗ |
| 速率限制 | 无 | ✗ |
| 字段选择 | 无（always full response） | ✗ |
| 分页 | 部分支持（events、candidates） | ✓ |
| 批量操作 | 无 | ✗ |

### 缺失的 BFF 职责
1. **聚合**: 多个后端数据源的组合查询
2. **数据裁剪**: 按前端需求减少响应大小
3. **缓存**: 热数据缓存（如设置、枚举）
4. **去重**: 避免重复数据
5. **排序**: 多维排序选项
6. **搜索**: 候选人搜索接口
7. **批量操作**: 批量更新评审项
8. **推荐**: 基于历史的智能推荐

---

## 6. WebSocket/SSE 现状

### 当前 SSE 实现
```
后端事件源
    ↓ (sse-starlette)
GET /api/workbench/events/stream (Server-Sent Events)
    ↓
前端 EventSource 监听
    ↓
Query Client 无效化
    ↓
自动重新获取数据
```

### 事件类型
```python
# event_routes.py:130-170
SESSION_SUMMARY_EVENTS = {
    'requirement_review_updated',
    'requirement_review_approved',
    'runtime_sourcing_queued',
    'runtime_sourcing_started',
    'runtime_sourcing_completed',
    'runtime_sourcing_failed',
    'source_connection_status_changed',
    'source_run_started',
    'source_run_completed',
    'source_run_failed'
}
```

### 对分层的影响
- **正面**: SSE 是单向的（后端→前端），易于扩展
- **负面**: 事件格式在代码中硬编码，无 schema 契约

---

## 7. 构建与部署现状

### 前端构建
```bash
# /apps/web-svelte
npm run build
# → 静态资源到 build/ 目录
```

### 后端应用构建
```bash
# 项目根目录
uv build
# → wheel 包 (dist/seektalent-*.whl)
```

### 打包方式
```python
# pyproject.toml:36-40
[project.scripts]
seektalent = "seektalent.cli:main"
seektalent-opencli = "seektalent.opencli_launcher:main"
seektalent-ui-api = "seektalent_ui.server:main"      # ← BFF API
seektalent-ui-maintenance = "seektalent_ui.maintenance:main"
```

### 前端与后端的集成
```python
# seektalent_ui/server.py:381-402
def mount_packaged_frontend(app: FastAPI) -> None:
    frontend_root = package_frontend_dir()
    if not frontend_available(frontend_root):
        return
    app.mount("/_app", StaticFiles(...))
    
    @app.get("/{path:path}", include_in_schema=False)
    async def packaged_frontend(path: str = "") -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (frontend_root / path).resolve(strict=False)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_root / "200.html")  # SPA fallback
```

**问题**: 前端必须编译进后端 wheel 包
- 耦合了部署周期
- 无法独立升级前端

---

## 8. 三层架构分离的 Gap 分析

### Gap 1: 逻辑分离（High Priority）

| 当前 | 所需 |
|------|------|
| 后端模型 → 直接序列化 | 后端模型 → BFF DTO → JSON |
| 响应转换分散 | 集中的转换管道（pipes） |
| 无转换中间层 | 明确的 DTO factory/mappers |

**推荐**: 创建 BFF 转换层

```
seektalent_ui/
├── transformers/
│   ├── __init__.py
│   ├── session_transformer.py
│   ├── candidate_transformer.py
│   ├── graph_transformer.py
│   └── ...
└── models/
    └── ... (existing)
```

### Gap 2: API 契约管理（High Priority）

| 当前 | 所需 |
|------|------|
| OpenAPI auto-gen | 明确的 API 版本（v1/v2） |
| 无 schema 版本 | 后向兼容性保证 |
| 前端直接导入 schema | API 契约分离 |

**推荐**: 
1. 引入 API 版本路由 (`/api/v1/...`)
2. 定义 OpenAPI 版本
3. 创建破坏性变更清单

### Gap 3: 前端独立部署（High Priority）

| 当前 | 所需 |
|------|------|
| 前端编译入后端 wheel | 独立的前端部署 |
| 单一部署单元 | 前端 + 后端分离部署 |
| 版本同步绑定 | 独立版本管理 |

**推荐**:
1. 前端生成单独的 npm 包
2. 后端从 npm 或 CDN 引用前端资源
3. 容器化分离部署

### Gap 4: 数据查询优化（Medium Priority）

| 当前 | 所需 |
|------|------|
| Over-fetch（总是完整响应） | GraphQL-like 字段选择 |
| 无缓存 | 响应缓存头 |
| 无批量操作 | 批量更新/查询端点 |

**推荐**: 
1. 添加 `?fields=...` 查询参数
2. 实现 HTTP 缓存头 (ETag/Cache-Control)
3. 批量操作端点

### Gap 5: 后端业务逻辑清晰界限（Medium Priority）

| 当前 | 所需 |
|------|------|
| 业务逻辑分布在 3 层 | 清晰的职责边界 |
| 前端有些状态逻辑 | 后端 source of truth |

**推荐**: 
1. 所有状态转换在后端
2. 前端只做展示和本地交互状态
3. 定义权限模型

### Gap 6: 实时通信标准化（Medium Priority）

| 当前 | 所需 |
|------|------|
| SSE 事件格式硬编码 | 标准事件 schema |
| 无事件版本控制 | 向后兼容的事件格式 |
| 单一 EventSource | 多个事件流的管理 |

**推荐**: 
1. 定义 `WorkbenchEvent` schema（版本化）
2. 使用 Event Sourcing 模式
3. 事件重放能力

---

## 9. 分层解耦推荐优先级与路径

### 第 1 优先级（必做，4-6 周）

#### 1.1 API 版本化
```python
# seektalent_ui/server.py
from fastapi import APIRouter

router_v1 = APIRouter(prefix="/api/v1")
router_v2 = APIRouter(prefix="/api/v2")

app.include_router(router_v1)
app.include_router(router_v2)  # 新功能
```

**工作量**: 2 周
**收益**: 破坏性变更可控

#### 1.2 前端独立构建与部署
```bash
# /apps/web-svelte/package.json
"build:standalone": "vite build && tar -czf dist-web.tar.gz build/",
"deploy:cdn": "aws s3 sync build/ s3://seektalent-frontend/"
```

**工作量**: 2 周
**收益**: 独立迭代周期

#### 1.3 集中转换管道
```python
# seektalent_ui/transformers/__init__.py
from .session_transformer import SessionTransformer
from .candidate_transformer import CandidateTransformer

class TransformationPipeline:
    def transform_session(self, db_session) -> WorkbenchSessionResponse:
        ...
    
    def transform_candidates(self, db_items) -> list[WorkbenchCandidateReviewItemResponse]:
        ...
```

**工作量**: 2 周
**收益**: 转换逻辑可测试、可维护

### 第 2 优先级（重要，6-8 周）

#### 2.1 响应字段选择
```python
# workbench_routes.py
@router.get("/api/v2/workbench/sessions/{session_id}/candidates")
def list_candidates(
    session_id: str,
    fields: str = Query(None),  # "id,name,score" 或 "*"
    ...
):
    # 只返回请求的字段
```

**工作量**: 3 周
**收益**: 减少网络传输 20-50%

#### 2.2 批量操作
```python
# workbench_routes.py
@router.post("/api/v2/workbench/candidates/batch-update")
def batch_update_candidates(
    updates: list[WorkbenchCandidateReviewItemUpdateRequest],
    ...
):
    # 事务性批量更新
```

**工作量**: 2 周
**收益**: 减少 HTTP 请求

#### 2.3 HTTP 缓存头
```python
from fastapi.responses import JSONResponse

response = JSONResponse(content=data)
response.headers["Cache-Control"] = "public, max-age=3600"
response.headers["ETag"] = compute_etag(data)
```

**工作量**: 1 周
**收益**: 浏览器缓存命中率提升

### 第 3 优先级（优化，8-12 周）

#### 3.1 GraphQL 网关（可选）
考虑添加 graphql-core 与 strawberry-graphql

**工作量**: 4-6 周
**收益**: 完全的字段选择与嵌套查询灵活性

#### 3.2 事件 Schema 版本化
```python
# seektalent_ui/models.py
class WorkbenchEventV1(BaseModel):
    version: Literal["v1"]
    eventId: str
    timestamp: datetime
    eventName: str
    payload: dict

class WorkbenchEventV2(WorkbenchEventV1):
    # 新增字段
    correlationId: str
```

**工作量**: 2 周
**收益**: 事件系统可演进

#### 3.3 后端聚合服务
创建专门的聚合端点，减少前端多请求

```python
# seektalent_ui/aggregation.py
@router.get("/api/v2/workbench/sessions/{session_id}/dashboard")
def get_dashboard(session_id: str):
    # 聚合: 会话 + 候选人统计 + 图概览 + 政策
    return {
        "session": ...,
        "candidateStats": ...,
        "graphPreview": ...,
        "sourcePolicies": ...
    }
```

**工作量**: 2-3 周
**收益**: 减少初始加载 HTTP 请求 5-10 个

---

## 10. 关键文件清单

### 后端核心
| 文件 | 行数 | 职责 |
|------|------|------|
| `/src/seektalent_ui/server.py` | 633 | FastAPI 应用工厂、Liepin API |
| `/src/seektalent_ui/models.py` | 908 | 响应 DTO 定义 |
| `/src/seektalent_ui/workbench_routes.py` | 826 | 工作台路由 |
| `/src/seektalent_ui/workbench_response.py` | 501 | 响应转换函数 |
| `/src/seektalent_ui/runtime_graph.py` | 776 | 运行时图转换 |
| `/src/seektalent_ui/resume_snapshot_projection.py` | 543 | 简历快照投影 |
| `/src/seektalent_ui/event_routes.py` | 294 | 事件 SSE 路由 |
| `/src/seektalent_ui/workbench_store.py` | 7799 | 数据持久化层（SQLite） |
| `/src/seektalent_ui/job_runner.py` | 448 | 后台任务执行 |

### 前端核心
| 文件 | 行数 | 职责 |
|------|------|------|
| `/apps/web-svelte/src/lib/api/client.ts` | 89 | HTTP 客户端、CSRF 管理 |
| `/apps/web-svelte/src/lib/api/workbench.ts` | 305 | API 调用集合 |
| `/apps/web-svelte/src/lib/api/schema.d.ts` | 95K | OpenAPI 生成的类型 |
| `/apps/web-svelte/src/lib/workbench/eventStream.ts` | 266 | SSE 管理、Query 无效化 |
| `/apps/web-svelte/src/lib/workbench/runtimeGraphView.ts` | - | 图形渲染逻辑 |
| `/apps/web-svelte/src/lib/components/*.svelte` | 39 files | UI 组件 |

### 构建与配置
| 文件 | 职责 |
|------|------|
| `/pyproject.toml` | 后端依赖、CLI 入口、构建配置 |
| `/apps/web-svelte/package.json` | 前端依赖、构建脚本 |
| `/apps/web-svelte/svelte.config.js` | SvelteKit 配置 |
| `/apps/web-svelte/vite.config.ts` | Vite 构建配置 |

---

## 11. 总体架构图（当前 vs 推荐）

### 当前架构（紧耦合）
```
┌─────────────────────────────────────┐
│        Frontend (Svelte SPA)        │
│  /apps/web-svelte                   │
│  - openapi-fetch 调用               │
│  - schema.d.ts (95KB 后端契约)      │
└──────────────┬──────────────────────┘
               │ HTTP/SSE
               ↓
┌──────────────────────────────────────────┐
│    API Layer (seektalent_ui)             │
│  - workbench_routes.py                   │
│  - *_response.py (转换分散)              │
│  - models.py (DTO)                       │
└──────────────┬──────────────────────────┘
               │ 直接查询/访问
               ↓
┌──────────────────────────────────────────┐
│    Backend Core (seektalent)             │
│  - runtime/ (业务逻辑)                   │
│  - models.py (业务模型，1442 行)         │
│  - evaluation.py                         │
│  - storage/ (持久化)                     │
└──────────────────────────────────────────┘

问题:
1. 前端导入后端 schema（强耦合）
2. API 无版本管理（破坏性变更风险）
3. 转换逻辑分散（难以维护）
4. 前端与后端部署同步（灵活性低）
5. 无字段选择（over-fetch）
```

### 推荐架构（解耦）
```
┌──────────────────────────────────────────┐
│        Frontend (独立部署)               │
│  /apps/web-svelte                        │
│  - 从 CDN 或 npm 加载前端包              │
│  - 本地 schema.d.ts (从 API gen)        │
│  - 无后端导入                            │
│  v1.2.3 ← 独立版本管理                   │
└──────────────────┬───────────────────────┘
                   │ HTTP/SSE + Versioned API
                   │ /api/v1/ 或 /api/v2/
                   ↓
┌──────────────────────────────────────────┐
│    BFF Layer (seektalent_ui v2)          │
│  - transformers/ (集中转换)              │
│  │  ├─ session_transformer.py            │
│  │  ├─ candidate_transformer.py          │
│  │  └─ graph_transformer.py              │
│  - models/ (版本化 DTO)                  │
│  │  ├─ v1/                               │
│  │  └─ v2/                               │
│  - aggregation.py (聚合层)               │
│  - versioned routes                      │
│  v2.0.0 ← 独立版本                       │
└──────────────┬──────────────────────────┘
               │ 清晰的 interface
               ↓
┌──────────────────────────────────────────┐
│    Backend Core (seektalent)             │
│  - runtime/ (业务逻辑，无变化)           │
│  - models.py (业务模型)                  │
│  - evaluation.py                         │
│  - storage/ (持久化)                     │
│  v0.6.8 ← 核心版本独立                   │
└──────────────────────────────────────────┘

收益:
1. 前端无后端导入（完全解耦）
2. 明确的版本管理（破坏性变更可控）
3. 集中的转换管道（可测试）
4. 独立部署（快速迭代）
5. 字段选择（省带宽）
6. 聚合端点（减少请求）
```

---

## 12. 迁移风险与缓解

### 风险 1: 破坏性变更（High）
**风险**: 迁移过程中前端与后端不兼容
**缓解**: 
- 先在 `/api/v2` 实现新接口，保留 `/api/v1` 兼容
- 前端能够同时支持两个版本
- 逐步灰度迁移（10% → 50% → 100%）

### 风险 2: 转换逻辑遗漏（Medium）
**风险**: 新转换层忽略某些业务规则
**缓解**:
- 添加单元测试，验证转换输出与当前 API 一致
- 集成测试对比 shadow traffic

### 风险 3: 性能回归（Medium）
**风险**: 新的聚合或转换引入性能问题
**缓解**:
- 性能基准测试（baseline）
- APM 监控（Datadog/New Relic）
- 分阶段部署，监控响应时间

### 风险 4: 前端部署工具链不成熟（Medium）
**风险**: 独立前端部署引入新的运维复杂度
**缓解**:
- 使用 Vercel/Netlify 等托管服务
- Docker 容器化前端
- CI/CD 流程完善

---

## 13. 执行计划（12 周）

### 第 1 周：规划与设计
- [ ] 确认架构决策
- [ ] 设计 DTO schema (v1 vs v2)
- [ ] 定义事件 schema 版本
- [ ] 创建迁移文档

### 第 2-3 周：API 版本化
- [ ] 创建 `/api/v1` 路由
- [ ] 复制现有路由到 v1（向后兼容）
- [ ] 添加版本检测中间件
- [ ] 更新前端指向 v1

### 第 4-5 周：转换层重构
- [ ] 创建 `seektalent_ui/transformers/`
- [ ] 实现 `SessionTransformer`, `CandidateTransformer`, `GraphTransformer`
- [ ] 添加单元测试（shadow API 对比）
- [ ] 集成 `/api/v2` 测试版本

### 第 6 周：前端独立部署
- [ ] 前端生成 npm 包或 CDN 资源
- [ ] 后端从 CDN 加载前端资源
- [ ] 测试独立构建和部署
- [ ] 文档化部署流程

### 第 7-8 周：优化（字段选择、缓存）
- [ ] 添加 `?fields=` 查询参数
- [ ] 实现 HTTP 缓存头
- [ ] 批量操作端点
- [ ] 聚合端点

### 第 9-10 周：测试与文档
- [ ] E2E 测试（前端 + v2 API）
- [ ] 性能基准测试
- [ ] API 文档更新
- [ ] 迁移指南

### 第 11-12 周：灰度与推出
- [ ] 10% 用户迁移到 v2
- [ ] 监控指标（性能、错误率）
- [ ] 50% 用户迁移
- [ ] 100% 迁移，关闭 v1

---

## 总结

SeekTalent 的分层现状处于**中等成熟度**：

**优点**:
- 前端与后端技术栈相对独立
- API 层已定义了清晰的 DTO
- 认证与授权实现完整
- SSE 提供了实时更新能力

**缺点**:
- API 响应 schema 与前端强耦合
- 无 API 版本管理
- 转换逻辑分散，难维护
- 前端编译进后端包，部署耦合
- 缺少 BFF 核心职责（聚合、缓存、字段选择）

**推荐优先级**:
1. **API 版本化** (v1/v2) - 2 周
2. **前端独立部署** - 2 周  
3. **集中转换管道** - 2 周
4. **字段选择 + 缓存** - 3 周
5. **聚合端点** - 2 周

实施这些改进后，SeekTalent 将达到**高度解耦的三层架构**，支持：
- 独立的前端迭代周期
- 无缝的 API 版本升级
- 可维护的转换逻辑
- 高效的网络传输
- 生产就绪的实时通信

