# Pi + DokoBot Local Provisioning For Liepin Dev Mode Design

## Summary

SeekTalent already has the Pi-first Liepin executor contract, automatic Liepin browser-session probe, Svelte Workbench parity surface, and Runtime multi-source lane merge path. The remaining local-dev blocker is operational: the product can know that the user's Chrome is logged into Liepin only after the configured Pi runtime can run the repo-owned Liepin skill and use DokoBot MCP tools from inside Pi.

This spec defines the local provisioning and readiness contract for that path. "Provisioning" in this slice means SeekTalent can create or repair a project-local Pi MCP config under the workspace `.pi/` directory; it does not mean installing Pi, installing the DokoBot Chrome extension, editing user-global Pi config, or letting Runtime call DokoBot directly.

The hard boundary is:

```text
Workbench -> Runtime -> LiepinProviderAdapter -> LiepinPiWorkerClient
  -> Pi RPC process -> repo-owned Liepin Pi skill
  -> DokoBot MCP registered inside Pi
  -> strict JSON envelope -> Runtime validation
```

DokoBot MCP is registered only in Pi. Runtime and Workbench do not import, invoke, or configure DokoBot as a product execution surface. They may validate safe configuration facts and observe Pi RPC tool events, but they never call DokoBot CLI/MCP directly.

## Current Problem

The current repository can truthfully show `通道不可用` when the local app is started without a working Pi execution path:

- `AppSettings.liepin_worker_mode` defaults to `disabled`.
- `SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent` requires a non-placeholder account-binding secret and valid Pi RPC command.
- `PiRpcAgentClient` already requires `--mode rpc`, `--no-session`, and a repo-owned skill path.
- `PiLiepinExecutor.probe_capabilities(...)` already accepts DokoBot readiness only when required tool names are observed in Pi RPC events.
- The user's Chrome can be logged into Liepin while SeekTalent still cannot see it, because the missing part is not Chrome login. The missing part is `Pi -> DokoBot MCP -> Chrome` being configured and reachable.

There is also historical code that can mislead future changes:

- `src/seektalent/providers/pi_agent/dokobot_client.py` shells out to `dokobot`.
- `src/seektalent/providers/pi_agent/capabilities.py` probes DokoBot CLI and action manifests directly.
- Those modules may remain as legacy diagnostics/tests, but they must not become the live product Runtime path for Liepin.

## External Constraints

Pi's published contract supports this design:

- RPC mode runs Pi over stdin/stdout JSONL with command responses and agent events.
- Pi's design keeps the core small and pushes workflow-specific behavior into extensions, skills, prompt templates, and packages.
- Pi skills are project/user resources that are injected into the agent prompt.
- Pi MCP configuration can live in project-local `.pi/mcp.json` or user-local `~/.pi/agent/mcp.json`; after adding MCP tools, Pi may need restart because tool metadata is cached at startup.

SeekTalent should therefore treat Pi as the configurable agent harness, not as code we fork or modify.

Primary references used for this planning slice:

- [Pi RPC Mode](https://pi.dev/docs/latest/rpc)
- [Pi Usage: Design Principles](https://pi.dev/docs/latest/usage)
- [Pi package docs showing project/user MCP config paths](https://pi.dev/packages/context-mode)
- [Pi package docs noting MCP tool metadata caching](https://pi.dev/packages/pi-subagents)

## Goals

- Make local Liepin readiness explainable before the user starts a source run.
- Provide a project-local setup command that can dry-run or write `.pi/mcp.json` for Pi, idempotently and without touching user-global Pi files.
- Provide a safe static `doctor`/dev-status contract that distinguishes:
  - Pi command missing
  - invalid Pi RPC command
  - missing repo-owned Liepin skill
  - missing or unreadable Pi MCP config
  - Pi MCP config present but no `dokobot` server declaration
- Provide an explicit live probe surface that distinguishes static setup from observed Pi/DokoBot runtime capability:
  - static setup configured
  - Pi present but DokoBot tools not observed through Pi RPC
  - Liepin browser session login required
- Keep DokoBot MCP registration inside Pi only.
- Keep Runtime's live product path limited to `PiRpcAgentClient` and strict Pi envelopes.
- Ensure the new Pi setup/live probe payloads, Workbench dev diagnostics, source cards, strategy graph, and start responses use safe reason codes and do not expose local paths or secrets. Existing unrelated `doctor --json` developer checks may continue to show non-sensitive local diagnostic paths.
- Let CTS continue independently when Liepin's Pi/DokoBot channel is unavailable.
- Keep Pi vanilla: no Pi fork, no invasive patching of Pi source, no embedded DokoBot transport in Runtime.

## Non-Goals

- Do not install Pi from inside SeekTalent.
- Do not install the DokoBot Chrome extension from inside SeekTalent.
- Do not edit user-global `~/.pi/agent/mcp.json` automatically.
- Do not introduce A2A.
- Do not revive managed-browser login relay, iframe, snapshot, or storage-state login paths.
- Do not make Runtime call DokoBot CLI, DokoBot MCP, Chrome DevTools, browser profile files, cookies, or Codex Chrome tools.
- Do not implement Liepin detail-open UI in this slice.

## Product Contract

### Configuration Surfaces

SeekTalent owns these config values:

- `SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent`
- `SEEKTALENT_LIEPIN_PI_COMMAND`, defaulting to `pi --mode rpc --no-session`
- `SEEKTALENT_LIEPIN_PI_SKILL_PATH`, defaulting to the repo-owned Liepin skill
- `SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME`, defaulting to `dokobot`
- `SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET`, required and non-placeholder for live `pi_agent`
- `SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH`, optional diagnostic pointer to the Pi-owned MCP config file, defaulting to `.pi/mcp.json` when present
- `SEEKTALENT_WORKSPACE_ROOT`, respected by diagnostics when settings cannot be constructed, so relative setup paths in an env file are resolved against the intended workspace instead of the current shell directory

SeekTalent does not own the DokoBot MCP server process. It only checks that Pi is configured to expose it and verifies observed Pi RPC tool events before running live Liepin work.

### Pi-Owned MCP Registration

The expected project-local Pi MCP file is:

```json
{
  "mcpServers": {
    "dokobot": {
      "command": "dokobot",
      "args": []
    }
  }
}
```

This file belongs to Pi. SeekTalent may read it for diagnostics when the user points `SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH` at it, but Runtime does not load it as a DokoBot client and does not invoke the command in it.

SeekTalent may also generate this project-local file through:

```bash
seektalent pi-agent init --project --dry-run
seektalent pi-agent init --project --write
```

The command must:

- write only under the active workspace `.pi/mcp.json`
- preserve unrelated existing MCP servers
- add or update only the expected DokoBot server entry
- refuse user-global Pi paths
- expose only safe status/reason payloads without local path leakage
- never execute `dokobot`

For this slice, the generated project MCP declaration intentionally keeps the server name, observed tool prefix, and command binary aligned to `dokobot`. `SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME` is the expected Pi tool prefix and MCP server key. The generated command remains the literal `dokobot`; supporting alternate commands such as `npx ...` is deferred until there is a real second browser helper or installation mode.

### Runtime Boundary

Live Liepin execution may only cross the process boundary through:

- `src/seektalent/providers/pi_agent/pi_external.py`
- `src/seektalent/providers/liepin/pi_executor.py`
- `src/seektalent/providers/liepin/pi_worker_client.py`

The following product paths must not import or call `DokoBotClient`, `DokoBotCapabilityProbe`, or a raw `dokobot` command:

- `src/seektalent/runtime`
- `src/seektalent_ui`
- `src/seektalent/providers/liepin`
- `src/seektalent/providers/registry.py`
- `src/seektalent/cli.py`, except for safe static diagnostics that inspect Pi-owned config files and do not run DokoBot

### Readiness Surfaces

Static setup and live capability are separate:

- Static setup: `seektalent doctor --json`, Workbench settings diagnostics, and `seektalent pi-agent init --project --dry-run` inspect env/config/files only. They can say whether Pi command, skill, account secret, and project-local MCP declaration are configured.
- Live capability: `seektalent doctor --live-pi-agent --json` or the existing source-run start/session probe may launch Pi RPC and require observed DokoBot tool events. This is the only layer that may report tool-observation status or Liepin browser-session status.

Static setup being `configured` must not be presented as proof that the live browser channel works.

### Readiness Reason Codes

Use these safe reason codes:

| Code | Meaning |
| --- | --- |
| `liepin_pi_disabled` | Liepin worker mode is not `pi_agent`. |
| `liepin_pi_command_missing` | The `pi` executable cannot be resolved. |
| `liepin_pi_command_invalid` | The configured Pi command is not RPC/no-session compatible. |
| `liepin_pi_skill_missing` | The repo-owned Liepin Pi skill is missing or unreadable. |
| `liepin_pi_account_secret_missing` | Account binding secret is missing or placeholder. |
| `liepin_pi_mcp_config_missing` | The Pi MCP config file is not present. |
| `liepin_pi_mcp_config_invalid` | The Pi MCP config is unreadable or not valid MCP JSON. |
| `liepin_pi_mcp_config_not_project_local` | A requested init target is outside the project-local `.pi/` directory. |
| `liepin_pi_dokobot_mcp_missing` | The Pi MCP config does not declare the expected DokoBot server. |
| `liepin_pi_dokobot_tool_unobserved` | Pi ran but required DokoBot tool events were not observed. |
| `liepin_browser_login_required` | Pi/DokoBot path is available but Liepin is not logged in. |
| `liepin_browser_probe_unavailable` | The Pi/DokoBot browser channel cannot be probed safely. |

`liepin_pi_disabled` and `liepin_pi_mcp_config_not_project_local` are static setup/init reasons. If a selected Liepin source blocks because static setup failed, Workbench source-state projection must preserve the corresponding safe reason code instead of collapsing it to `failed_provider_error`.

Pi local setup payloads, live Pi probe payloads, Workbench responses/events, and DOM content must not include raw command paths, local MCP file paths, cookies, Chrome profile paths, raw Pi events, raw DokoBot output, provider account ids, or artifact filesystem paths. Existing `doctor --json` is a developer diagnostic and may include non-sensitive package/output/data-root paths from other checks; this slice must not add path leakage to the new Pi setup/live probe checks.

## UX Contract

Primary recruiter UI should not add another "connect Liepin" button and should not mention `Pi`, `DokoBot`, or `MCP`. If Liepin was selected and the source is blocked, the UI should say in product language:

- When browser channel setup is unavailable: "浏览器检索通道不可用，请到本机设置检查浏览器助手后重试。"
- When Liepin login is missing: "请在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。"
- When account mismatch occurs: "当前 Chrome 中的猎聘账号与此工作台绑定不一致，请切换账号后重试。"

Developer diagnostics may live in `settings`, `doctor`, or `pi-agent` CLI output. Those diagnostic surfaces may mention Pi and DokoBot MCP.

## Acceptance Criteria

- `seektalent pi-agent init --project --dry-run` reports the project-local Pi MCP change it would make without writing a file and without leaking local paths.
- `seektalent pi-agent init --project --write` creates or updates workspace `.pi/mcp.json`, preserves unrelated MCP servers, refuses user-global Pi paths, refuses to overwrite invalid existing MCP JSON, and never executes DokoBot.
- `seektalent doctor --json` includes a `liepin_pi_local_setup` check when Liepin is configured or requested.
- `seektalent doctor --json` reports static setup only; it does not imply observed Pi/DokoBot capability.
- `seektalent doctor --live-pi-agent --json` reports observed Pi/DokoBot capability separately from static setup.
- Doctor reports `liepin_pi_command_missing` when `SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent` and the configured Pi executable cannot be resolved.
- Doctor reports `liepin_pi_mcp_config_missing` when `pi_agent` mode is requested and no Pi MCP config can be found at the configured/default path.
- Doctor reports `liepin_pi_dokobot_mcp_missing` when the Pi MCP config has no expected `dokobot` server entry.
- Doctor respects `SEEKTALENT_WORKSPACE_ROOT` when settings cannot be constructed, so relative env-file paths resolve under the intended workspace.
- Workbench dev diagnostics expose the same safe component statuses without local paths or secrets.
- `PiLiepinExecutor.probe_capabilities(...)` continues to accept readiness only from strict Pi envelope plus observed Pi RPC tool events.
- Capability proof failures distinguish declared-tool failure from observed-tool failure: missing/invalid declarations remain generic backend unavailable, while declared-but-unobserved DokoBot tools produce `liepin_pi_dokobot_tool_unobserved`.
- Live Pi session probing preserves browser-session reason semantics: setup/capability failures surface as Pi/DokoBot setup reasons, while Liepin login state surfaces as `liepin_browser_login_required`, `liepin_browser_probe_unavailable`, or `liepin_browser_account_mismatch`.
- Workbench runtime source-state projection preserves new `liepin_pi_*` and `liepin_browser_*` safe reason codes through `runtimeSourceState.sources[].reasonCode`.
- Runtime/Workbench/liepin/CLI product code has a static boundary test proving it does not import `DokoBotClient`, `DokoBotCapabilityProbe`, or raw DokoBot command execution. The only CLI exception is writing static Pi MCP JSON without executing DokoBot.
- Existing direct DokoBot CLI modules are either renamed/marked as legacy diagnostics or fenced so they cannot be used by the live product path.
- Main Workbench source cards and run notes use business-facing copy and do not contain `Pi`, `DokoBot`, or `MCP` terms; settings/doctor diagnostics may use those terms.
- If Pi is missing or not configured, CTS still starts and Liepin alone blocks with safe reason copy.
- If Pi is present and DokoBot MCP is declared but observed tool events are missing, Liepin blocks with `liepin_pi_dokobot_tool_unobserved` or `liepin_browser_probe_unavailable`.
- If Pi/DokoBot is available and Liepin session probe returns ready, Workbench binds the Liepin connection and starts the Liepin lane through the existing automatic session probe path.
- No public response, event, DOM, Pi setup payload, or live Pi probe payload leaks `cookie`, `storageState`, `Authorization`, raw provider account id, raw Pi output, raw DokoBot output, or local artifact paths.

## Linked Plan

- [2026-05-19-pi-dokobot-local-provisioning-liepin-dev-mode.md](../plans/2026-05-19-pi-dokobot-local-provisioning-liepin-dev-mode.md)
