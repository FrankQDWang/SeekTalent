# Pi MCP Adapter DokoBot Bridge Design

## Summary

SeekTalent's Liepin live path must run through vanilla Pi plus repo-owned extensions and skills. Pi is the agent harness. DokoBot is a browser capability available only inside Pi through an MCP bridge. Runtime and Workbench continue to see only a Pi RPC command, observed Pi tool events, artifact refs, and strict JSON envelopes.

This slice corrects the current gap: project-local `.pi/mcp.json` can be generated, but Pi core does not load MCP by itself. A Pi MCP adapter extension must be loaded into the Pi RPC process, and the DokoBot MCP server command/tool names must be confirmed before live Liepin search can be considered configured.

The execution boundary is:

```text
Workbench -> Runtime -> LiepinProviderAdapter -> LiepinPiWorkerClient
  -> Pi RPC process
  -> repo-owned Bailian provider extension
  -> repo-owned Liepin skill
  -> pinned Pi MCP adapter extension
  -> DokoBot MCP server inside Pi
  -> strict JSON envelope -> Runtime validation
```

## Current Evidence

Pi package documentation supports the extension-based approach:

- `pi-mcp-adapter` is an MCP adapter extension for Pi and supports Pi-owned `.pi/mcp.json`, direct tools, a proxy `mcp` tool, and lazy/eager server lifecycle. It explicitly describes `.pi/mcp.json` as a Pi project override and says `directTools` can expose selected MCP tools directly in Pi's tool list.
- `pi-mcp-extension` is a smaller generic MCP client extension that reads global/project config and bridges MCP tools into Pi.
- Pi package pages warn that Pi packages can execute code and influence agent behavior, so third-party package selection must be explicit and pinned.
- DokoBot's public Agent Collaboration Mode describes MCP-compatible browser actions such as navigation, clicking, JavaScript execution, and screenshots.

Local repository evidence:

- `apps/web-svelte/package.json` already pins the repo-local Pi runtime package.
- `src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts` provides the Runtime-aligned DeepSeek/Bailian provider extension for Pi.
- `scripts/start-dev-workbench.sh` starts Pi with the provider extension but does not yet load a Pi MCP adapter extension.
- `src/seektalent/providers/pi_agent/local_setup.py` can generate `.pi/mcp.json`, but its generated `{"command": "dokobot", "args": []}` declaration is only a static assumption until the actual DokoBot MCP server command is proven.
- A live Pi probe currently observes built-in Pi tools such as `read`/`bash`, not DokoBot MCP browser tools.

Primary references:

- [pi-mcp-adapter package](https://pi.dev/packages/pi-mcp-adapter)
- [pi-mcp-extension package](https://pi.dev/packages/pi-mcp-extension)
- [DokoBot Agent Features](https://dokobot.ai/zh-TW/help/agent-features)

## Goals

- Keep Pi vanilla: do not fork or patch Pi source.
- Pin a Pi MCP adapter dependency in the repo instead of relying on ambient global Pi packages or network installation at runtime.
- Load the pinned MCP adapter extension in the Pi RPC command used by dev-mode Liepin.
- Keep the Bailian/DeepSeek provider config in the root `.env` and inject it into the backend/Pi process only.
- Confirm the real DokoBot MCP server startup command and browser tool names before declaring live Liepin configured.
- Support explicit DokoBot MCP command/tool configuration from the root `.env` when the default cannot be proven.
- Keep DokoBot MCP registered only inside Pi; Runtime and Workbench must not call DokoBot CLI/MCP directly.
- Preserve CTS independence when the Pi/DokoBot bridge is unavailable.
- Fail closed with safe reason codes instead of silently falling back to legacy managed-browser, direct DokoBot CLI, or fake fixtures.

## Non-Goals

- Do not replace Pi with another agent harness.
- Do not introduce A2A.
- Do not make Runtime an MCP client.
- Do not make Workbench call DokoBot, Chrome DevTools, browser profile files, cookies, or Codex Chrome tools.
- Do not revive managed-browser login relay, iframe, snapshot, storage-state, or provider-cookie paths.
- Do not install the DokoBot Chrome extension automatically.
- Do not make a live Liepin run depend on globally installed Pi packages that are not represented in repo config.

## Product Contract

### Pi Runtime Command

The dev launcher and config diagnostics must build a Pi command that includes:

```bash
<repo-local-pi-bin> --mode rpc --no-session \
  --extension <repo-bailian-provider-extension> \
  --extension <repo-pinned-pi-mcp-adapter-extension> \
  --provider bailian \
  --model <root-env-pi-or-runtime-model>
```

`PiRpcAgentClient.build_pi_rpc_argv(...)` remains responsible for appending:

```bash
--no-skills --skill <repo-owned-liepin-skill>
```

`SEEKTALENT_LIEPIN_PI_COMMAND` may override the command, but a live `pi_agent` command is invalid unless it includes:

- `--mode rpc`
- `--no-session`
- the repo-owned Bailian provider extension
- the pinned MCP adapter extension
- no inline `--skill`

Alternative provider-extension or MCP-adapter markers are deferred. This slice validates the repo-owned Bailian/DeepSeek extension and pinned `pi-mcp-adapter` path because they are the only extension paths already represented in the repo and aligned with the root `.env` model settings.

### MCP Adapter Choice

Use `pi-mcp-adapter` as the default bridge for SeekTalent because:

- it supports `.pi/mcp.json` Pi project overrides;
- it supports lazy MCP server startup;
- it supports `directTools`, allowing a small set of DokoBot browser tools to appear directly in Pi's tool list;
- it offers a compact proxy path for later expansion, while this slice uses selected direct tools only.

`pi-mcp-extension` remains an acceptable fallback only if `pi-mcp-adapter` cannot load the required DokoBot MCP server or cannot expose stable browser tool events. The fallback must be an explicit plan revision, not an automatic runtime fallback.

### DokoBot MCP Configuration

Static setup must stop treating this config as proven:

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

Instead, the config model must distinguish:

- MCP server key: default `dokobot`
- DokoBot MCP command: explicit command binary or script path
- DokoBot MCP args: explicit argv list
- expected browser tool names: explicit direct tool names or a confirmed default set
- observed Pi tool names: the names Runtime expects in Pi RPC `tool_execution_*` events

New root `.env` knobs:

```dotenv
SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME=dokobot
SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND=
SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON=[]
SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON=[]
SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON=[]
```

If `SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND` is empty and the repo cannot prove a supported DokoBot MCP command from local tooling, static setup reports `liepin_pi_dokobot_mcp_command_missing`. It must not write a fake executable command and must not mark the live channel configured.

When a command is configured, `seektalent pi-agent init --project --write` writes a Pi project override:

```json
{
  "mcpServers": {
    "dokobot": {
      "command": "<configured-command>",
      "args": ["<configured>", "<args>"],
      "lifecycle": "lazy",
      "directTools": ["<configured-tool>", "<configured-tool>"]
    }
  }
}
```

If `directTools` and the expected observed tool names are empty, this slice treats the DokoBot MCP bridge as not configured and reports `liepin_pi_dokobot_mcp_tool_names_missing`. It does not infer tool names from natural-language Pi output and does not accept the adapter proxy path in this implementation.

### Live Capability Proof

Live readiness requires all of the following:

1. Pi starts in RPC mode with the provider extension and MCP adapter extension loaded.
2. The repo-owned Liepin skill is loaded by `PiRpcAgentClient`.
3. The adapter sees a DokoBot MCP server declaration in Pi config.
4. Pi RPC events include the expected observed DokoBot browser tool names.
5. The strict Pi capability envelope names the same tools and allowed host scope.

Protected adapter proxy proof is deferred. It may be added later only if the adapter can emit a protected trace containing server name, original MCP tool name, safe action kind, and no raw page content, cookies, account identifiers, or filesystem paths. Until then, a proxy-only `mcp` tool observation is not enough to mark Liepin live search configured.

### Safe Reason Codes

Add or preserve these safe codes:

| Code | Meaning |
| --- | --- |
| `liepin_pi_mcp_adapter_missing` | The Pi MCP adapter extension is not installed or not present in the configured command. |
| `liepin_pi_mcp_adapter_unavailable` | Pi starts but the adapter cannot initialize. |
| `liepin_pi_dokobot_mcp_command_missing` | Static setup has no proven DokoBot MCP server command. |
| `liepin_pi_dokobot_mcp_tool_names_missing` | Static setup has no configured or discovered DokoBot browser tool names. |
| `liepin_pi_dokobot_tool_unobserved` | Pi ran but required DokoBot browser tool events were not observed. |

Existing safe codes from the previous slice remain valid, including `liepin_pi_command_missing`, `liepin_pi_command_invalid`, `liepin_pi_skill_missing`, `liepin_pi_mcp_config_missing`, `liepin_pi_dokobot_mcp_missing`, `liepin_browser_login_required`, `liepin_browser_probe_unavailable`, and `liepin_browser_account_mismatch`.

### Runtime Boundary

Allowed product path:

- `src/seektalent/providers/pi_agent/pi_external.py`
- `src/seektalent/providers/liepin/pi_executor.py`
- `src/seektalent/providers/liepin/pi_worker_client.py`
- `src/seektalent/providers/liepin/client.py`
- `scripts/start-dev-workbench.sh`

Disallowed product path:

- importing `DokoBotClient`
- importing `DokoBotCapabilityProbe`
- executing `dokobot` from Runtime/Workbench/Liepin adapter code
- reading Chrome profile/cookie/storage files
- calling browser automation from Workbench

The only code allowed to mention the DokoBot MCP command is static configuration/provisioning code that writes or validates Pi-owned config without executing it. Existing legacy managed-browser routes are not expanded by this slice and are not evidence that the new Pi/DokoBot path may call browser or DokoBot APIs directly.

## Acceptance Criteria

- `apps/web-svelte/package.json` pins a Pi MCP adapter dependency.
- `scripts/start-dev-workbench.sh` builds a Pi command that includes both the Bailian provider extension and the pinned MCP adapter extension.
- `scripts/start-dev-workbench.sh` still starts the backend and frontend when the DokoBot MCP command is missing; Liepin blocks with a safe reason instead of aborting the whole dev workbench.
- `SEEKTALENT_LIEPIN_PI_COMMAND` generated by the dev launcher contains no provider API key or account-binding secret.
- `AppSettings` rejects `liepin_worker_mode=pi_agent` commands that omit either the repo-owned Bailian provider extension marker or the pinned MCP adapter marker.
- `seektalent pi-agent init --project --dry-run` reports `liepin_pi_dokobot_mcp_command_missing` when no DokoBot MCP command is configured or proven.
- `seektalent pi-agent init --project --write` writes `.pi/mcp.json` only when a DokoBot MCP command is configured or proven.
- `.pi/mcp.json` generation preserves unrelated MCP servers and writes `lifecycle: "lazy"`.
- `directTools` is written only when configured/discovered, never guessed.
- The Pi local setup portion of `seektalent doctor --json` exposes static setup reasons without local paths or secrets. Existing developer diagnostics such as local data-root posture may continue to report non-secret local paths.
- Dev-mode readiness diagnostics consume the same root `.env` DokoBot MCP command and tool-name fields as `AppSettings`.
- `seektalent doctor --live-pi-agent --json` distinguishes adapter missing, DokoBot command missing, DokoBot tool names missing, and observed-tool failure.
- `PiLiepinExecutor.probe_capabilities(...)` accepts DokoBot readiness only when direct observed tool names match configured expectations.
- CLI inspection/config discovery lists the new `SEEKTALENT_LIEPIN_DOKOBOT_*` env vars without exposing configured secrets.
- Runtime source-state projection preserves new `liepin_pi_*` reason codes.
- CTS still starts when Liepin blocks because the Pi MCP adapter/DokoBot bridge is missing.
- Main Workbench UI continues to use business-facing copy and does not mention `Pi`, `DokoBot`, or `MCP`.
- Boundary tests prove Runtime/Workbench/Liepin product paths do not call DokoBot directly.
- A skipped-by-default live smoke documents the exact command to verify that Pi sees DokoBot MCP browser tools once the DokoBot MCP server command is known.

## Deferred Work

- Packaging DokoBot Chrome extension installation.
- Supporting multiple browser helper backends.
- Supporting user-global Pi MCP config mutation.
- Supporting protected `pi-mcp-adapter` proxy proof when direct DokoBot tools cannot be exposed.
- Migrating from `pi-mcp-adapter` to `pi-mcp-extension`.
- A2A or cross-process agent negotiation beyond Pi RPC.

## Linked Plan

- [2026-05-19-pi-mcp-adapter-dokobot-bridge.md](../plans/2026-05-19-pi-mcp-adapter-dokobot-bridge.md)
