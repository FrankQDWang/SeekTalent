# Configuration

`SeekTalent v0.3 phase 3 bootstrap/execution/ranking core` keeps CTS and local path settings while the user-facing runtime remains gated. Old model, reflection, search-budget, and UI settings are still gone.

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
| `SEEKTALENT_RUNS_DIR` | No | `runs` | Output root used by `doctor`; `run` is currently gated. |

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

## Validation

Use:

```bash
seektalent doctor
```

`doctor` only checks:

- the packaged CTS spec path
- settings loading
- the configured runs directory
- CTS credentials, unless mock mode is enabled
- that the repo is still in gated runtime mode

## Related docs

- [CLI](cli.md)
- [Architecture](architecture.md)
- [Outputs](outputs.md)
