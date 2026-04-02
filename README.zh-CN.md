# cv-match

<p>
  <a href="./README.md"><img src="https://img.shields.io/badge/Language-English-0A66C2" alt="English"></a>
  <a href="#简体中文"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D4380D" alt="简体中文"></a>
</p>

## 简体中文

`cv-match` 是一个面向本地使用的实验型开源简历匹配 Agent。它会把 `JD + 寻访须知` 转成一个可审计的多轮 shortlist，包含需求抽取、受控 CTS 检索、单简历评分、反思和最终结果生成。

这个项目现在已经可以使用，但边界是刻意收紧的：

- 它优先服务本地迭代和可复盘性，不是托管式多租户平台。
- 它通过带认证的 CTS 搜索完成检索。
- 对外入口有 CLI 和一个最小 Web UI。

## 核心特性

- 用一个确定性的 Python Agent 包住少量 LLM 步骤
- 提供真实 CTS 接入，并明确要求凭证
- 每次运行都会把结构化审计产物落到 `runs/`
- 提供一个最小本地 Web UI，支持输入 JD、寻访偏好并浏览 shortlist
- 所有模型配置统一使用 `provider:model` 格式

## 快速开始

推荐最终用户优先使用本地 Web UI。

### 1. 打开终端

- macOS：按 `Command + Space`，输入 `Terminal`，然后打开它。
- Windows：从开始菜单打开 `Windows Terminal` 或 `PowerShell`。

### 2. 进入项目目录

```bash
cd path/to/cv-match
```

### 3. 确认你已经具备这些前置条件

- Python `3.12+`
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 和 `pnpm`
- 至少一组可用的 LLM provider 凭证
- CTS 凭证

### 4. 安装依赖

```bash
uv sync
```

### 5. 复制 `.env.example` 为 `.env`

```bash
cp .env.example .env
```

Windows PowerShell：

```bash
Copy-Item .env.example .env
```

### 6. 填写 `.env` 里的必填值

大多数用户不需要先改 `.env.example` 里的默认模型名。

你必须填写：

- 一组与你当前模型配置匹配的 LLM provider key
- `CVMATCH_CTS_TENANT_KEY`
- `CVMATCH_CTS_TENANT_SECRET`

示例：

```dotenv
OPENAI_API_KEY=your-openai-key
CVMATCH_CTS_TENANT_KEY=your-cts-tenant-key
CVMATCH_CTS_TENANT_SECRET=your-cts-tenant-secret
```

如果你保留默认的 `openai-responses:*` 模型，那么只需要填写 `OPENAI_API_KEY` 这一组 provider 凭证。

### 7. 启动后端

```bash
uv run cv-match-ui-api
```

### 8. 在另一个终端启动前端

```bash
cd path/to/cv-match/apps/web-user-lite
pnpm install
pnpm dev
```

### 9. 在浏览器中打开

```text
http://127.0.0.1:5176
```

## 安装

普通使用：

```bash
uv sync
```

## 配置

程序会自动从 `.env` 读取环境变量。

通常需要配置三类变量：

- LLM provider 凭证，例如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GOOGLE_API_KEY`
- CTS 连接配置，例如 `CVMATCH_CTS_BASE_URL`、`CVMATCH_CTS_TENANT_KEY`、`CVMATCH_CTS_TENANT_SECRET`
- Agent 行为配置，例如模型 ID、轮次上限、并发和输出目录

最小可运行配置：

- 一组与你当前模型配置匹配的 provider key
- `CVMATCH_CTS_TENANT_KEY`
- `CVMATCH_CTS_TENANT_SECRET`

完整配置说明见：

- [docs/configuration.md](docs/configuration.md)

几个重要规则：

- 模型变量必须使用 `provider:model` 格式。
- 如果你使用 `openai`、`openai-chat` 或 `openai-responses` 模型，需要设置 `OPENAI_API_KEY`。
- 如果你使用 `anthropic:*`，需要设置 `ANTHROPIC_API_KEY`。
- 如果你使用 `google-gla:*`，需要设置 `GOOGLE_API_KEY`。

## CLI 用法

从文件读取输入：

```bash
uv run cv-match --jd-file examples/jd.md --notes-file examples/notes.md --real-cts
```

直接传文本：

```bash
uv run cv-match --jd "Python agent engineer" --notes "Shanghai preferred" --real-cts
```

CLI 输出会包含：

- 最终 markdown 结果
- `run_id`
- `run_directory`
- `trace_log`

完整 CLI 说明见：

- [docs/cli.md](docs/cli.md)

## Web UI

仓库内置了一个最小本地 Web UI：

- 后端 API：`cv-match-ui-api`
- 前端目录：`apps/web-user-lite`
- 默认后端端口：`8011`
- 默认前端端口：`5176`

先启动后端：

```bash
uv run cv-match-ui-api
```

再在另一个终端启动前端：

```bash
cd apps/web-user-lite
pnpm install
pnpm dev
```

浏览器打开：

```text
http://127.0.0.1:5176
```

完整 UI 说明见：

- [docs/ui.md](docs/ui.md)

## 输出产物

每次运行都会在 `runs/` 下生成一个带时间戳的目录，常见产物包括：

- `trace.log`
- `events.jsonl`
- `run_config.json`
- `final_candidates.json`
- `final_answer.md`
- 每轮的 controller / retrieval / reflection / scoring 产物

完整输出说明见：

- [docs/outputs.md](docs/outputs.md)

## 当前边界

当前限制是刻意设计的：

- 这是一个实验型本地 Agent，不是托管产品。
- Web UI 只是一个轻量本地 shim，不是完整招聘平台。
- CTS adapter 只覆盖当前仓库里已经实现的字段和语义。
- 这个 Agent 优先保证可审计、可复盘的确定性控制流，而不是开放式自治 agent。

## 文档导航

- [Configuration](docs/configuration.md)
- [UI](docs/ui.md)
- [CLI](docs/cli.md)
- [Outputs](docs/outputs.md)
- [Architecture](docs/architecture.md)
- [Development](docs/development.md)

历史版本设计文档保留在 `docs/v-*` 下，不在这次整理中改动。

## 许可证

本项目采用 GNU Affero General Public License v3.0。

详见 [LICENSE](LICENSE)。
