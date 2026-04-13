# SeekTalent

<p>
  <a href="./README.md"><img src="https://img.shields.io/badge/Language-English-0A66C2" alt="English"></a>
  <a href="#简体中文"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D4380D" alt="简体中文"></a>
</p>

## 简体中文

`SeekTalent` 当前运行在 `v0.3.3 active` runtime。`docs/v-0.3.3/` 描述的是当前 `HEAD` 的 active runtime 和 trace surface；`docs/v-0.3.2/` 仅作为 cutover 前的冻结基线保留，方便对照。`HEAD` 当前包含 deterministic requirement normalization、bootstrap 内核、persistent-anchor frontier control、reviewer-ready output，以及 checked-in offline artifacts。

现在仓库里真正存在的东西：

- `docs/v-0.3.3/SYSTEM_MODEL.md` 是当前 `HEAD` 的 active canonical spec
- `docs/v-0.3.3/IMPLEMENTATION_OWNERS.md` 是当前 `HEAD` 的 active implementation anchor
- `docs/v-0.3.3/RUNTIME_SEQUENCE.md` 是当前 runtime 的时序视图
- `docs/v-0.3.2/` 仅保留为 cutover 前的冻结基线
- `src/seektalent/models.py` 持有当前稳定 runtime contract
- `src/seektalent/requirements/normalization.py` 负责 `SearchInputTruth` 和标准化后的 `RequirementSheet`
- `src/seektalent/bootstrap.py` 负责内部 round-0 bootstrap 主链
- `src/seektalent/retrieval/filter_projection.py` 负责把 `SearchExecutionPlan_t` 安全投影到 CTS native filters
- `src/seektalent/clients/cts_client.py` 直接返回 `RetrievedCandidate_t`
- `src/seektalent/retrieval/candidate_projection.py` 负责构造 `SearchExecutionResult_t`
- `src/seektalent/runtime/orchestrator.py` 负责完整 runtime loop，并写出 run artifacts
- `seektalent run` 和 `run_match(...)` 会直接返回 `SearchRunBundle`

已经删除的东西：

- 旧的 `v0.2` controller / reflection / scoring / finalize runtime
- `v0.2` prompt bundles 和对应的 LLM wiring
- 被删 contract 的 compatibility aliases

## 安装

从本地仓库安装：

```bash
uv build
pipx install dist/seektalent-0.3.4-py3-none-any.whl
```

或者装进现有 Python 环境：

```bash
pip install dist/seektalent-0.3.4-py3-none-any.whl
```

## 快速开始

先写出 starter env：

```bash
seektalent init
```

`seektalent init` 会写出随安装包一起分发的 starter 模板。仓库根目录的 [.env.example](/Users/frankqdwang/Agents/SeekTalent/.env.example) 仍然是这份模板的源码来源。

真实 CTS 模式下最少需要：

```dotenv
OPENAI_API_KEY=your-openai-key
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

现在 5 个 LLM callpoint 都可以通过 `.env` 独立切换。具体字段见 [docs/configuration.md](/Users/frankqdwang/Agents/SeekTalent/docs/configuration.md)。

本地启动 rerank API：

```bash
uv run --group rerank seektalent-rerank-api
```

检查本地 runtime surface：

```bash
seektalent doctor
seektalent inspect --json
```

面向人类的入口：

```bash
seektalent
```

在 TTY 里这会直接打开一个聊天式 `Textual` 会话。先粘贴 `JD` 并按 `Shift+Enter`，再按需补充 `notes`。整个 run 会在同一条 transcript 中持续滚动，最终结果输出后会话结束。

面向 agent 的入口：

```bash
seektalent run --request-file ./request.json --json --progress jsonl
```

最小 request 文件：

```json
{
  "job_description": "Senior agent engineer with Python and LLM orchestration experience",
  "hiring_notes": "Shanghai preferred; startup background is a plus",
  "top_k": 10,
  "round_budget": 6
}
```

`run --json` 仍然返回完整 `SearchRunBundle`。稳定的产品结果入口是 `final_result.final_candidate_cards`。

## Python API

包导出 `run_match(...)` 和 `run_match_async(...)`，并直接返回 `SearchRunBundle`：

```python
from seektalent import AppSettings, run_match

result = run_match(
    job_description="Python agent engineer",
    hiring_notes="Shanghai preferred",
    settings=AppSettings(mock_cts=True),
    env_file=None,
)
print(result.final_result.stop_reason)
print(result.run_dir)
```

## 命令

- `seektalent`（仅在 TTY 下；直接打开一次性聊天式 Textual 会话）
- `seektalent run`
- `seektalent doctor`
- `seektalent init`
- `seektalent version`
- `seektalent update`
- `seektalent inspect`

## 文档

- [docs/v-0.3.3/SYSTEM_MODEL.md](docs/v-0.3.3/SYSTEM_MODEL.md)
- [docs/v-0.3.3/SYSTEM_MODEL.notion.md](docs/v-0.3.3/SYSTEM_MODEL.notion.md)
- [docs/v-0.3.3/IMPLEMENTATION_OWNERS.md](docs/v-0.3.3/IMPLEMENTATION_OWNERS.md)
- [docs/v-0.3.3/RUNTIME_SEQUENCE.md](docs/v-0.3.3/RUNTIME_SEQUENCE.md)
- [docs/v-0.3.2/SYSTEM_MODEL.md](docs/v-0.3.2/SYSTEM_MODEL.md)
- [docs/v-0.3.2/IMPLEMENTATION_OWNERS.md](docs/v-0.3.2/IMPLEMENTATION_OWNERS.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/cli.md](docs/cli.md)
- [docs/outputs.md](docs/outputs.md)
- [docs/development.md](docs/development.md)
- [docs/_archive/v-0.3.1/implementation-checklist.md](docs/_archive/v-0.3.1/implementation-checklist.md)
