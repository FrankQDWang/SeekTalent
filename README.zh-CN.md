# SeekTalent

<p>
  <a href="./README.md"><img src="https://img.shields.io/badge/Language-English-0A66C2" alt="English"></a>
  <a href="#简体中文"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D4380D" alt="简体中文"></a>
</p>

## 简体中文

`SeekTalent` 是一个本地优先的简历匹配引擎。它会把必填的岗位名称、`JD` 和可选的寻访须知转成一个可审计的多轮 shortlist，包含需求抽取、受控 CTS 检索、单简历评分、反思和最终结果生成。

当前产品形态是刻意收紧的：

- 主产品是本地 CLI
- 同一套 runtime 也能作为 Python 依赖被调用
- 仓库里仍然有一个最小本地 Web UI，但它不是主入口

## 核心特性

- 可安装 CLI，稳定命令为 `run`、`init`、`doctor`、`version`、`update`、`inspect`
- 稳定 Python 入口：`run_match(...)` 和 `run_match_async(...)`
- 默认把结构化运行产物写到 `runs/`
- 所有模型配置统一使用 `provider:model`
- 提供真实 CTS 接入，并明确要求凭证

## 快速开始

### 前置条件

- Python `3.12+`
- 至少一组可用的 LLM provider 凭证
- 真实 CTS 模式下需要 CTS 凭证

### 安装为 CLI

从本地仓库安装：

```bash
uv build
pipx install dist/seektalent-0.4.1-py3-none-any.whl
```

如果你更希望装进现有 Python 环境：

```bash
pip install dist/seektalent-0.4.1-py3-none-any.whl
```

默认安装形态是 OpenAI-only。它只包含 `pydantic-ai-slim[openai]`，因此开箱即用的只有 `openai:*`、`openai-chat:*`、`openai-responses:*` 这几类模型，以及兼容 `OPENAI_BASE_URL` 的 OpenAI-compatible 端点。

### 生成启动配置

```bash
seektalent init
```

在源码仓库里，`.env.example` 是唯一应该编辑的 env 模板；`src/seektalent/default.env` 只保留为安装包镜像，这样发布后的 wheel 仍然能执行 `seektalent init`。

### 填写 `.env` 里的必填值

至少需要：

```dotenv
OPENAI_API_KEY=your-openai-key
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

如果保留默认的 `openai-responses:*` 模型，只需要 `OPENAI_API_KEY` 这一组 provider 凭证。

如果后续要启用 `anthropic:*` 或 `google-gla:*`，需要先把安装扩展成包含对应的 `pydantic-ai-slim[...]` extras，不能依赖当前默认安装。

### 检查本地环境

```bash
seektalent doctor
```

### 推荐的黑盒使用顺序

```bash
seektalent --help
seektalent doctor
seektalent run --job-title-file ./job_title.md --jd-file ./jd.md
seektalent inspect --json
seektalent update
```

### 运行一次工作流

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience"
```

如果你需要补充寻访偏好或排除条件，再加 `notes`：

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience" \
  --notes "Shanghai preferred, avoid pure frontend profiles"
```

默认输出是人类可读文本。给包壳程序或脚本时，使用机器输出：

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --json
```

### 打印升级说明

```bash
seektalent update
```

### 查看发布版 CLI 的机器可读描述

```bash
seektalent inspect --json
```

## 安装路径

### 给终端用户

推荐：

```bash
pipx install dist/seektalent-0.4.1-py3-none-any.whl
```

这样会直接得到 `seektalent` 命令。

### 给 Python 集成方

```bash
pip install dist/seektalent-0.4.1-py3-none-any.whl
```

然后：

```python
from seektalent import run_match

result = run_match(
    job_title="Python agent engineer",
    jd="Python agent engineer",
)

print(result.final_markdown)
print(result.run_dir)
```

## CLI

规范入口是：

```bash
seektalent run --help
```

可用命令：

- `seektalent run`
- `seektalent init`
- `seektalent doctor`
- `seektalent version`
- `seektalent update`
- `seektalent inspect`

推荐的黑盒调用顺序：

- `seektalent --help`
- `seektalent doctor`
- `seektalent run`
- `seektalent inspect --json`
- `seektalent update`

`run` 的关键参数：

- `--job-title` 或 `--job-title-file`，用于必填岗位名称
- `--jd` 或 `--jd-file`，用于必填 JD
- `--notes` 或 `--notes-file`，用于可选的寻访偏好
- `--env-file`
- `--output-dir`
- `--json`

默认输出根目录是当前工作目录下的 `./runs`。如果要单次覆盖：

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --output-dir ./outputs
```

完整 CLI 说明见：

- [docs/cli.zh-CN.md](docs/cli.zh-CN.md)

## 如何包壳 `SeekTalent`

目前明确稳定的包壳方式有两种：

### 包 CLI

运行：

```bash
seektalent run --job-title "..." --jd "..." --json
```

然后读取 stdout 的单个 JSON 对象。

### 包 Python 库

```python
from seektalent import run_match

result = run_match(job_title="...", jd="...", notes="...")
payload = result.final_result.model_dump(mode="json")
```

如果需要补充寻访偏好，再传 `notes="..."`；如果 JD 已经足够，可以直接省略。

如果你要做自己的 API 服务、桌面端或工作流壳子，优先走这两条稳定入口，不要直接绑内部模块细节。

## 配置

默认会从 `.env` 读取环境变量。通常需要配置：

- provider 凭证，例如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GOOGLE_API_KEY`
- CTS 配置，例如 `SEEKTALENT_CTS_BASE_URL`、`SEEKTALENT_CTS_TENANT_KEY`、`SEEKTALENT_CTS_TENANT_SECRET`
- runtime 配置，例如轮次上限、并发和输出目录

完整配置说明见：

- [docs/configuration.md](docs/configuration.md)

几个重要规则：

- 模型变量必须使用 `provider:model`
- OpenAI 系列模型需要 `OPENAI_API_KEY`
- `anthropic:*` 需要 `ANTHROPIC_API_KEY`
- `google-gla:*` 需要 `GOOGLE_API_KEY`

## Web UI

仓库里仍然包含一个最小本地 Web UI：

- 后端 API：`seektalent-ui-api`
- 前端目录：`apps/web-user-lite`
- 默认后端端口：`8011`
- 默认前端端口：`5176`

启动后端：

```bash
uv run seektalent-ui-api
```

在另一个终端启动前端：

```bash
cd apps/web-user-lite
pnpm install
pnpm dev
```

然后在浏览器打开：

```text
http://127.0.0.1:5176
```

## 输出产物

每次运行默认都会在 `runs/` 下生成一个带时间戳的目录，常见产物包括：

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

- 这是一个实验型本地引擎，不是托管式多租户产品
- Web UI 只是一个本地薄壳，不是完整招聘平台
- CTS adapter 只覆盖当前仓库已经实现的字段和语义
- runtime 优先保证可审计、可复盘的确定性控制流，而不是开放式自治 agent

## 文档导航

- [Configuration](docs/configuration.md)
- [CLI](docs/cli.zh-CN.md)
- [UI](docs/ui.md)
- [Outputs](docs/outputs.md)
- [Architecture](docs/architecture.md)
- [Development](docs/development.md)

历史版本设计文档保留在 `docs/v-*` 下。

## 许可证

本项目采用 GNU Affero General Public License v3.0。

详见 [LICENSE](LICENSE)。
