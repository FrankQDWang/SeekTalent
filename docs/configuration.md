# Configuration

`SeekTalent v0.3.1 phase 6 offline artifacts active` keeps CTS, rerank API, local path settings, and an active runtime manifest. Old phase-5-only knobs are still gone.

## Starter env

Generate the template with:

```bash
seektalent init
```

The packaged template is:

```dotenv
SEEKTALENT_CTS_BASE_URL=https://link.hewa.cn
SEEKTALENT_CTS_TENANT_KEY=
SEEKTALENT_CTS_TENANT_SECRET=
SEEKTALENT_CTS_TIMEOUT_SECONDS=20
SEEKTALENT_CTS_SPEC_PATH=cts.validated.yaml
SEEKTALENT_MOCK_CTS=false
SEEKTALENT_RERANK_BASE_URL=http://127.0.0.1:8012
SEEKTALENT_RERANK_TIMEOUT_SECONDS=20
SEEKTALENT_RUNS_DIR=runs
```

## Variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SEEKTALENT_CTS_BASE_URL` | No | `https://link.hewa.cn` | Base URL for the real CTS service. |
| `SEEKTALENT_CTS_TENANT_KEY` | Required in real CTS mode | empty | Used as the `tenant_key` header. |
| `SEEKTALENT_CTS_TENANT_SECRET` | Required in real CTS mode | empty | Used as the `tenant_secret` header. |
| `SEEKTALENT_CTS_TIMEOUT_SECONDS` | No | `20` | HTTP timeout for CTS calls. |
| `SEEKTALENT_CTS_SPEC_PATH` | No | `cts.validated.yaml` | Uses the packaged spec when left at the default value. |
| `SEEKTALENT_MOCK_CTS` | No | `false` | Enables the local mock CTS corpus. |
| `SEEKTALENT_RERANK_BASE_URL` | No | `http://127.0.0.1:8012` | Base URL for the local rerank HTTP API. |
| `SEEKTALENT_RERANK_TIMEOUT_SECONDS` | No | `20` | HTTP timeout for rerank requests. |
| `SEEKTALENT_RUNS_DIR` | No | `runs` | Output root for `run` artifacts and `doctor`. |

## Modes

### Real CTS

At minimum:

```dotenv
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

### Mock CTS

For local bridge work and tests:

```dotenv
SEEKTALENT_MOCK_CTS=true
```

The runtime still needs a rerank service. Start the local API with:

```bash
uv run --group rerank seektalent-rerank-api
```

## Validation

Use:

```bash
seektalent doctor
```

`doctor` checks:

- the packaged CTS spec path
- settings loading
- the configured runs directory
- the active runtime manifest (`artifacts/runtime/active.json`)
- CTS credentials, unless mock mode is enabled
- rerank base URL and timeout settings

## Runtime artifact owners

- `artifacts/runtime/active.json` binds the active `knowledge_pack_ids`, `policy_id`, and `calibration_id`.
- `artifacts/runtime/policies/<policy_id>.json` owns `BusinessPolicyPack`.
- `artifacts/knowledge/packs/` owns the round-0 routing and keyword-injection context for each active domain.

## Related docs

- [CLI](cli.md)
- [Architecture](architecture.md)
- [Outputs](outputs.md)
