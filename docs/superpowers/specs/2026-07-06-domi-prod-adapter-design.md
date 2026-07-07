# Domi Prod Adapter Design

## Summary

This slice turns the current first-stage Domi smoke into a practical PyPI/Workbench adapter path for a prepared user machine.

The prepared-machine assumptions are explicit:

- Domi is already installed on the user's machine.
- SeekTalent is installed into, or launched by, Domi's bundled Python runtime.
- OpenCLI uses Domi's bundled Node runtime.
- Chrome already has the OpenCLI extension installed and connected.
- The user is already logged in to Liepin in Chrome.
- Domi JWT is provided to SeekTalent through the terminal environment during manual testing.

This is still not the final Domi product integration. It does not read Domi Electron storage, does not implement a Domi plugin protocol, and does not install Chrome extensions.

## Goal

Make `seektalent workbench` and the Domi-specific launcher run consistently when the user provides Domi Python, Domi Node, and Domi JWT through environment variables, with clear Chinese startup failures before the Workbench server launches.

## Non-Goals

- Do not read Domi Electron storage for JWT discovery.
- Do not implement the final Domi launch protocol.
- Do not implement or replace the browser backend with `domi/`.
- Do not register Chrome native messaging hosts.
- Do not install the OpenCLI Chrome extension.
- Do not make a live Liepin recruiting run part of automated CI.
- Do not download or run a SeekTalent-managed Node runtime in Prod/Domi Workbench.

## Runtime Contract

The Domi adapter requires these variables for the manual test path:

```env
SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi
SEEKTALENT_DOMI_JWT=<manually pasted Domi JWT>
SEEKTALENT_DOMI_LLM_BASE_URL=<optional Domi LLM proxy base URL>
SEEKTALENT_DOMI_LLM_CHANNEL=seek_talent
SEEKTALENT_DOMI_NODE=<path to Domi node executable or node bin directory>
```

Existing aliases remain accepted for local operator convenience:

```env
DOMI_NODE=<path to Domi node executable or node bin directory>
DOMI_PYTHON=<path to Domi Python executable>
```

`DOMI_PYTHON` is a launch-time operator convenience. SeekTalent does not switch interpreters after it has already started. The validated path is to run the installed SeekTalent entrypoint using Domi Python, so `sys.executable` is the Domi Python.

## Cut 1: Helper Python And Startup Messages

`build_workbench_command_env()` must always set:

```env
SEEKTALENT_PYTHON=<sys.executable>
```

This fixes the OpenCLI extension helper boundary. The TypeScript helper already uses `process.env.SEEKTALENT_PYTHON || "python"` before running:

```text
python -m seektalent.providers.liepin.opencli_browser_cli
```

In a PyPI/Domi run, `sys.executable` is the Domi Python. Passing it through prevents the helper from falling back to an unrelated system Python on Windows or macOS.

Workbench preflight keeps existing reason codes but changes user-facing messages to Chinese for the prepared-machine failures:

- missing direct LLM key;
- missing Domi JWT;
- OpenCLI bootstrap failure;
- OpenCLI extension disconnected;
- OpenCLI daemon stale or not running;
- Liepin login required;
- Liepin identity selection;
- Liepin risk or captcha page;
- Liepin search page not ready.

Reason codes remain stable for tests, support, and future integration.

## Cut 2: Domi Node And Domi Launcher

`seektalent.opencli_launcher` is hard-cut to Domi Node for the Prod/Domi Workbench path.

The Domi Node path is read from one of these variables:

- `SEEKTALENT_OPENCLI_NODE`, `SEEKTALENT_DOMI_NODE`, or `DOMI_NODE` is set.

Missing Domi Node is a hard startup failure with reason `domi_node_missing`. SeekTalent must not silently download any replacement Node runtime.

When a Domi Node path is supplied, OpenCLI itself can still be installed under SeekTalent's existing OpenCLI runtime root. The product premise is that Node comes from Domi; it does not require OpenCLI's npm package files to live inside the Domi application directory.

Add a small launcher entrypoint:

```text
seektalent-domi
```

The launcher only normalizes the Domi environment and delegates to `seektalent workbench`. It must:

- require `SEEKTALENT_DOMI_JWT`;
- require Domi Node through `SEEKTALENT_DOMI_NODE` or `DOMI_NODE`;
- set `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi`;
- set `SEEKTALENT_OPENCLI_NODE=<resolved Domi Node path>`;
- delegate to the existing Workbench command with all CLI arguments preserved.

It must not read Domi storage or guess tokens.

## Error Handling

Startup failures print:

```text
reason_code=<stable-code> <Chinese message>
```

Credential and runtime messages must not include raw JWT values, Domi storage paths containing secrets, or provider response bodies.

The important prepared-machine failures are:

| Reason code | Meaning |
| --- | --- |
| `seektalent_domi_jwt_missing` | The terminal did not provide a Domi JWT. |
| `domi_node_missing` | Domi Node was required but no usable Node path was provided. |
| `liepin_opencli_extension_disconnected` | Chrome's OpenCLI extension is not connected. |
| `liepin_opencli_login_required` | Liepin is not logged in in Chrome. |
| `liepin_opencli_identity_intercept` | Liepin requires identity or company selection. |
| `liepin_opencli_risk_page` | Liepin risk verification or captcha is blocking automation. |

## Tests

Automated tests cover the deterministic contract:

- product env sets `SEEKTALENT_PYTHON` to `sys.executable`;
- Workbench preflight emits Chinese messages while preserving reason codes;
- OpenCLI runtime setup requires a supplied Domi Node and never downloads a replacement Node runtime;
- missing Domi Node fails with `domi_node_missing`;
- `seektalent-domi` normalizes Domi env and delegates to Workbench;
- the built wheel exposes the `seektalent-domi` console script.

Live browser tests remain manual because they require a real Chrome profile, installed OpenCLI extension, valid Liepin login, and Domi JWT.

## Manual Acceptance

Mac development acceptance:

```bash
export DOMI_PYTHON="/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python"
export DOMI_NODE="<path to Domi node>"
export SEEKTALENT_DOMI_JWT="<manually pasted Domi JWT>"
export SEEKTALENT_DOMI_LLM_CHANNEL="seek_talent"

"${DOMI_PYTHON}" -m pip install -U seektalent
"${DOMI_PYTHON}" -m seektalent.domi_workbench --port 8011
```

Windows manual acceptance uses the same environment names and runs the installed `seektalent-domi.exe` or `python -m seektalent.domi_workbench` from the Domi Python environment.

Acceptance conditions:

- missing Domi JWT fails before server launch with `reason_code=seektalent_domi_jwt_missing`;
- missing Domi Node fails before server launch with `reason_code=domi_node_missing`;
- OpenCLI helper subprocesses receive `SEEKTALENT_PYTHON=<Domi Python>`;
- OpenCLI uses Domi Node;
- Domi JWT is used as the LLM API key through the existing Domi LLM transport;
- OpenCLI extension disconnected and Liepin login missing produce Chinese messages with stable reason codes.
