# Domi x SeekTalent 本地接口草案

状态：草案，仅用于 Domi 侧和 SeekTalent 侧对齐方向。后续实现可以调整路径、字段和安全策略。

## 目标

Domi Electron 客户端负责承载用户配置入口和启动入口。SeekTalent 负责本机检测、配置落地、运行服务启动、浏览器连接助手检测，以及猎聘登录状态检测。

用户不需要复制环境变量到终端，也不需要登录第二套 SeekTalent 账号。Domi 通过本机 API 调用 SeekTalent，完成配置、检测和启动。

## 边界

- Domi 负责展示 UI、收集用户输入、保存或转交配置、展示检测结果。
- SeekTalent 负责校验配置、启动本地运行服务、管理本地数据空间、调用浏览器连接助手。
- Domi 不直接读取 Chrome profile、cookie、LocalStorage 或猎聘页面内容。
- Domi 不直接写 SeekTalent SQLite 数据库。
- Chrome 插件安装仍由用户或企业策略完成。Domi 可以打开安装页并检测安装结果。

## 本机服务

建议 SeekTalent Local API 只监听本机回环地址：

```text
http://127.0.0.1:<port>
```

端口可以由 Domi 启动 SeekTalent 时指定，也可以由 SeekTalent 选择可用端口后通过启动响应返回。

## 安全约定

所有 API 请求都带本机会话 token：

```http
Authorization: Bearer <local_session_token>
```

建议策略：

- token 由 Domi 启动 SeekTalent 本地服务时生成或交换得到。
- token 只在本机有效，随本地进程生命周期失效。
- API 只绑定 `127.0.0.1`，默认不监听 LAN 地址。
- 对浏览器发起的请求做 Origin 校验。
- 密钥类配置不出现在日志、诊断 JSON、错误消息或前端事件中。

## 用户可见配置项

| 技术依赖 | 用户看到的名字 | 用户说明 |
| --- | --- | --- |
| 前后端服务 | 本地运行服务 | 负责在你的电脑上运行 SeekTalent |
| SQLite / workspace root | 本地数据空间 | 保存任务记录、候选摘要和运行结果 |
| LLM key | AI 模型密钥 | 用于理解 JD、评分和生成匹配理由 |
| CTS credentials | CTS 数据源授权 | 用于从 CTS 检索候选人 |
| Chrome 插件 / WtsCLI / Bridge | 浏览器连接助手 | 用于读取你已登录浏览器中的猎聘页面 |
| Liepin login | 猎聘登录状态 | 确认当前 Chrome 是否已登录猎聘 |

## 状态枚举

通用状态：

```text
unknown
checking
ready
missing
invalid
warning
blocked
running
stopped
```

建议 Domi 显示文案：

| 状态 | 用户文案 |
| --- | --- |
| `ready` | 已就绪 |
| `missing` | 未配置 |
| `invalid` | 配置无效 |
| `warning` | 需确认 |
| `blocked` | 不可用 |
| `checking` | 检测中 |
| `running` | 运行中 |
| `stopped` | 未运行 |

## 接口草案

### 1. 健康检查

```http
GET /local/health
```

用于确认 SeekTalent Local API 是否可访问。

响应：

```json
{
  "status": "ready",
  "app": "SeekTalent",
  "version": "0.2.4",
  "apiVersion": "local-api-draft-v1"
}
```

### 2. 获取整体配置状态

```http
GET /local/setup/status
```

响应：

```json
{
  "overallStatus": "missing",
  "readinessScore": 50,
  "components": [
    {
      "id": "local_service",
      "label": "本地运行服务",
      "status": "ready",
      "required": true,
      "description": "负责在你的电脑上运行 SeekTalent"
    },
    {
      "id": "data_space",
      "label": "本地数据空间",
      "status": "ready",
      "required": true,
      "description": "保存任务记录、候选摘要和运行结果"
    },
    {
      "id": "ai_model_key",
      "label": "AI 模型密钥",
      "status": "missing",
      "required": true,
      "description": "用于理解 JD、评分和生成匹配理由"
    },
    {
      "id": "cts_auth",
      "label": "CTS 数据源授权",
      "status": "ready",
      "required": false,
      "description": "用于从 CTS 检索候选人"
    },
    {
      "id": "browser_helper",
      "label": "浏览器连接助手",
      "status": "missing",
      "required": false,
      "description": "用于读取你已登录浏览器中的猎聘页面"
    },
    {
      "id": "liepin_login",
      "label": "猎聘登录状态",
      "status": "warning",
      "required": false,
      "description": "确认当前 Chrome 是否已登录猎聘"
    }
  ],
  "canLaunch": false,
  "canLaunchPartial": true,
  "nextAction": {
    "componentId": "ai_model_key",
    "label": "先完成：AI 模型密钥"
  }
}
```

### 3. 保存配置

```http
POST /local/setup/config
Content-Type: application/json
```

请求：

```json
{
  "dataSpacePath": "/Users/frank/Documents/SeekTalent",
  "aiModel": {
    "provider": "openai-compatible",
    "baseUrl": "https://api.example.com/v1",
    "apiKey": "secret"
  },
  "cts": {
    "tenant": "tenant-id",
    "key": "secret",
    "secret": "secret"
  }
}
```

响应：

```json
{
  "status": "saved",
  "updatedComponents": ["data_space", "ai_model_key", "cts_auth"]
}
```

约定：

- 密钥字段只允许写入，不在后续 API 中原样返回。
- Domi 侧可以显示“已保存”，但不显示密钥明文。
- SeekTalent 侧负责写入本地安全存储或本地配置文件。

### 4. 执行环境检测

```http
POST /local/setup/check
Content-Type: application/json
```

请求：

```json
{
  "components": ["local_service", "data_space", "ai_model_key", "cts_auth", "browser_helper", "liepin_login"]
}
```

响应：

```json
{
  "overallStatus": "missing",
  "readinessScore": 50,
  "results": [
    {
      "id": "ai_model_key",
      "status": "missing",
      "safeReasonCode": "ai_model_key_missing",
      "message": "AI 模型密钥未配置"
    },
    {
      "id": "browser_helper",
      "status": "missing",
      "safeReasonCode": "browser_helper_not_connected",
      "message": "浏览器连接助手未连接"
    }
  ]
}
```

### 5. 检测浏览器连接助手

```http
POST /local/browser/check
Content-Type: application/json
```

请求：

```json
{
  "source": "liepin"
}
```

响应：

```json
{
  "browserHelper": {
    "status": "ready",
    "lastSeenAt": "2026-05-28T02:30:00+08:00"
  },
  "chrome": {
    "status": "ready"
  },
  "liepinLogin": {
    "status": "warning",
    "safeReasonCode": "liepin_login_required",
    "message": "请先在 Chrome 登录猎聘"
  }
}
```

约定：

- 不返回 cookie、页面 HTML、简历内容或原始 DOM。
- 只返回安全状态码和用户可理解消息。
- 猎聘账号不一致、风险验证、登录过期等都用安全 reason code 表示。

### 6. 启动 SeekTalent

```http
POST /local/launch
Content-Type: application/json
```

请求：

```json
{
  "mode": "full"
}
```

`mode` 可选：

| mode | 含义 |
| --- | --- |
| `full` | 启动 CTS 和猎聘 |
| `available_sources` | 只启动已就绪的数据源 |

响应：

```json
{
  "status": "running",
  "mode": "available_sources",
  "webUiUrl": "http://127.0.0.1:5178",
  "backendUrl": "http://127.0.0.1:8012",
  "enabledSources": ["cts"],
  "disabledSources": [
    {
      "source": "liepin",
      "safeReasonCode": "browser_helper_not_connected",
      "message": "浏览器连接助手未连接"
    }
  ]
}
```

### 7. 获取运行状态

```http
GET /local/runtime/status
```

响应：

```json
{
  "status": "running",
  "webUiUrl": "http://127.0.0.1:5178",
  "backendUrl": "http://127.0.0.1:8012",
  "startedAt": "2026-05-28T02:31:00+08:00",
  "enabledSources": ["cts"]
}
```

### 8. 停止本地服务

```http
POST /local/runtime/stop
```

响应：

```json
{
  "status": "stopped"
}
```

## Domi 推荐交互

1. Domi 打开 SeekTalent 启动页。
2. Domi 调 `GET /local/health`。
3. Domi 调 `GET /local/setup/status`，展示依赖状态。
4. 用户在 Domi 内填写 AI 模型密钥、CTS 授权和本地数据空间。
5. Domi 调 `POST /local/setup/config` 保存配置。
6. Domi 调 `POST /local/setup/check` 重新检测。
7. 用户按需安装 Chrome 插件并登录猎聘。
8. Domi 调 `POST /local/browser/check` 检测浏览器连接助手和猎聘登录状态。
9. Domi 调 `POST /local/launch` 启动本地 SeekTalent Web UI。
10. Domi 打开 `webUiUrl`，或在 Electron 内嵌本地 Web UI。

## 错误响应

统一错误格式：

```json
{
  "error": {
    "code": "browser_helper_not_connected",
    "message": "浏览器连接助手未连接",
    "componentId": "browser_helper",
    "retryable": true
  }
}
```

错误消息必须是安全文案，不包含密钥、cookie、完整路径、页面原文、简历内容或供应商原始响应。

## 待确认问题

- Domi 是否负责保存密钥，还是只转交给 SeekTalent 本地安全存储。
- SeekTalent Local API 端口由 Domi 指定，还是由 SeekTalent 动态选择后返回。
- Domi 是否内嵌 SeekTalent Web UI，还是用系统浏览器打开本地 URL。
- Chrome 插件由企业策略安装、Web Store 安装，还是先支持开发者模式安装。
- 浏览器连接助手采用 Native Messaging host，还是本地 HTTP / WebSocket bridge。
- 是否需要 Domi 侧展示高级诊断 JSON。
- 是否需要支持离线/只用 CTS 的启动模式作为默认兜底。
