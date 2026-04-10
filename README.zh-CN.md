# SeekTalent

<p>
  <a href="./README.md"><img src="https://img.shields.io/badge/Language-English-0A66C2" alt="English"></a>
  <a href="#简体中文"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D4380D" alt="简体中文"></a>
</p>

## 简体中文

`SeekTalent` 现在以 `v0.3.1 phase 6 offline artifacts` 为当前基线。当前 `HEAD` 提供 `docs/v-0.3.1` 里的稳定 contract、deterministic requirement normalization、bootstrap 内核、execution/ranking、frontier control、已经接通的 CLI/API runtime 表面，以及 checked-in offline artifacts。

现在仓库里真正存在的东西：

- `docs/v-0.3.1` 是唯一活的规格入口
- `src/seektalent/models.py` 持有稳定 runtime contract
- `src/seektalent/requirements/normalization.py` 负责 `SearchInputTruth` 和 `RequirementSheet`
- `src/seektalent/bootstrap.py` 负责内部 round-0 bootstrap 主链
- `src/seektalent/retrieval/filter_projection.py` 负责把 `SearchExecutionPlan_t` 安全投影到 CTS
- `src/seektalent/clients/cts_client.py` 直接返回 `RetrievedCandidate_t`
- `src/seektalent/retrieval/candidate_projection.py` 负责构造 `SearchExecutionResult_t`
- `src/seektalent/runtime/orchestrator.py` 负责完整 runtime loop，并写出 run artifacts
- `seektalent run` 和 `run_match(...)` 会直接返回 `SearchRunBundle`

已经删除的东西：

- 旧的 `v0.2` controller / reflection / scoring / finalize 主链
- 旧 Web UI 和 UI API
- `v0.2` prompt bundle 和 LLM wiring
- 所有被删 contract 的兼容别名

## 安装

从本地仓库安装：

```bash
uv build
pipx install dist/seektalent-0.3.0a1-py3-none-any.whl
```

或者装进现有 Python 环境：

```bash
pip install dist/seektalent-0.3.0a1-py3-none-any.whl
```

## 快速开始

先生成默认 env：

```bash
seektalent init
```

真实 CTS 模式下最少需要：

```dotenv
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

本地启动 rerank API：

```bash
uv run --group rerank seektalent-rerank-api
```

检查本地 runtime 表面：

```bash
seektalent doctor
seektalent inspect --json
```

执行一个 case：

```bash
seektalent run --jd-file ./jd.md
```

默认 stdout 会打印四行：`run_dir`、`stop_reason`、逗号拼接的 shortlist ids、以及 `run_summary`。

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

- `seektalent run`
- `seektalent doctor`
- `seektalent init`
- `seektalent version`
- `seektalent update`
- `seektalent inspect`

## 文档

- [docs/architecture.md](docs/architecture.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/cli.md](docs/cli.md)
- [docs/outputs.md](docs/outputs.md)
- [docs/development.md](docs/development.md)
- [docs/v-0.3.1/implementation-checklist.md](docs/v-0.3.1/implementation-checklist.md)
