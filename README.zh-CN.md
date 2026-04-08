# SeekTalent

<p>
  <a href="./README.md"><img src="https://img.shields.io/badge/Language-English-0A66C2" alt="English"></a>
  <a href="#简体中文"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D4380D" alt="简体中文"></a>
</p>

## 简体中文

`SeekTalent` 现在处于一次破坏式的 `v0.3 phase 1` 切换。当前 `HEAD` 只保留 `docs/v-0.3` 里的稳定 contract、纯 deterministic 的 requirement normalization、CTS bridge、真实/模拟 CTS client，以及一个很薄的 CLI/API skeleton。

现在仓库里真正存在的东西：

- `docs/v-0.3` 是唯一活的规格入口
- `src/seektalent/models.py` 持有 phase 1 contract
- `src/seektalent/requirements/normalization.py` 负责 `SearchInputTruth` 和 `RequirementSheet`
- `src/seektalent/retrieval/filter_projection.py` 负责把 `SearchExecutionPlan_t` 安全投影到 CTS
- `src/seektalent/clients/cts_client.py` 直接返回 `RetrievedCandidate_t`
- `src/seektalent/retrieval/candidate_projection.py` 负责构造 `SearchExecutionResult_t`
- `seektalent run` 和 `run_match(...)` 目前都故意被 phase gate 挡住

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

检查本地 phase 1 表面：

```bash
seektalent doctor
seektalent inspect --json
```

`run` 入口还在，但当前会明确 fail fast：

```bash
seektalent run --jd-file ./jd.md
```

今天的预期行为就是抛出 `Phase1RuntimeGateError`，明确说明完整 runtime 要等 phase 2+。

## Python API

包仍然导出 `run_match(...)` 和 `run_match_async(...)`，但它们现在都会抛同一个 phase gate：

```python
from seektalent import AppSettings, Phase1RuntimeGateError, run_match

try:
    run_match(
        job_description="Python agent engineer",
        hiring_notes="Shanghai preferred",
        settings=AppSettings(mock_cts=True),
        env_file=None,
    )
except Phase1RuntimeGateError as exc:
    print(exc)
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
- [docs/v-0.3/implementation-checklist.md](docs/v-0.3/implementation-checklist.md)
