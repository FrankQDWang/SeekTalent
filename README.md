# SeekTalent

<p>
  <a href="#english"><img src="https://img.shields.io/badge/Language-English-0A66C2" alt="English"></a>
  <a href="./README.zh-CN.md"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-D4380D" alt="简体中文"></a>
</p>

## English

`SeekTalent` is currently on the `v0.3.1 phase 6 offline artifacts` baseline. `HEAD` ships the stable contracts in `docs/v-0.3.1`, deterministic requirement normalization, the bootstrap core, execution/ranking, frontier control, a live CLI/API runtime surface, and checked-in offline artifacts.

What exists today:

- `docs/v-0.3.1` is the only active spec surface
- `src/seektalent/models.py` holds the stable runtime contracts
- `src/seektalent/requirements/normalization.py` builds `SearchInputTruth` and normalized `RequirementSheet`
- `src/seektalent/bootstrap.py` runs the internal round-0 bootstrap flow
- `src/seektalent/retrieval/filter_projection.py` projects `SearchExecutionPlan_t` into CTS-safe native filters
- `src/seektalent/clients/cts_client.py` returns `RetrievedCandidate_t`
- `src/seektalent/retrieval/candidate_projection.py` builds `SearchExecutionResult_t`
- `src/seektalent/runtime/orchestrator.py` runs the full runtime loop and writes run artifacts
- `seektalent run` and `run_match(...)` return `SearchRunBundle`

What does not exist anymore:

- the old `v0.2` controller / reflection / scoring / finalize runtime
- the old web UI and UI API
- prompt bundles and LLM wiring from `v0.2`
- compatibility aliases for deleted contracts

## Install

From a local checkout:

```bash
uv build
pipx install dist/seektalent-0.3.0a1-py3-none-any.whl
```

Or into an existing Python environment:

```bash
pip install dist/seektalent-0.3.0a1-py3-none-any.whl
```

## Quick Start

Write a starter env file:

```bash
seektalent init
```

Minimal env values for real CTS mode:

```dotenv
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

Run the local rerank API:

```bash
uv run --group rerank seektalent-rerank-api
```

Check the local runtime surface:

```bash
seektalent doctor
seektalent inspect --json
```

Run a case:

```bash
seektalent run --jd-file ./jd.md
```

Default stdout is four lines: `run_dir`, `stop_reason`, comma-joined shortlist ids, and `run_summary`.

## Python API

The package exports `run_match(...)` and `run_match_async(...)` and returns `SearchRunBundle`:

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

## Commands

- `seektalent run`
- `seektalent doctor`
- `seektalent init`
- `seektalent version`
- `seektalent update`
- `seektalent inspect`

## Docs

- [docs/architecture.md](docs/architecture.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/cli.md](docs/cli.md)
- [docs/outputs.md](docs/outputs.md)
- [docs/development.md](docs/development.md)
- [docs/v-0.3.1/implementation-checklist.md](docs/v-0.3.1/implementation-checklist.md)
