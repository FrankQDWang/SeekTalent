# Local Product Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SeekTalent's local CLI plus local workbench product contract explicit in docs, startup checks, and inspect/doctor output.

**Architecture:** Keep the current source-checkout startup path intact while adding a stable local-product vocabulary and data-root safety checks. The implementation is documentation-first with small CLI/config tests so future packaging and entitlement work has a fixed boundary to target.

**Tech Stack:** Python 3.12, Pydantic settings, argparse CLI, pytest, existing FastAPI workbench docs.

**Spec:** `docs/superpowers/specs/2026-05-13-local-product-contract-design.md`

---

## Relationship To Other Plans

Run this plan before `docs/superpowers/plans/2026-05-13-entitlement-and-key-control.md`. It establishes the local-product vocabulary, data-root posture, and inspect/doctor shape that entitlement status will extend later.

Do not include repo slimming in this implementation. Older cloud-service-oriented code should be inventoried in a separate repo-slimming audit after the local product and entitlement boundaries are explicit.

This plan is the first local-product contract slice. It must not absorb full SQLite lifecycle work, a local web security rewrite, JSON Schema/OpenAPI formalization, platform-specific installer directory selection, provider connector pluginization, entitlement leases, or the complete packaged launcher argument surface. Record those follow-ups in root `TODOS.md` so they remain visible without expanding this slice.

## File Structure

- Modify: `README.md`
  - Describe SeekTalent as a local recruiter workbench with CLI and local UI entrypoints.
- Modify: `docs/ui.md`
  - Keep workbench docs first-class and align setup wording with local product contract.
- Modify: `docs/cli.md`
  - Add local product entrypoint notes and packaged-startup target wording.
- Modify: `docs/configuration.md`
  - Document local data root safety and runtime mode expectations.
- Modify: `TODOS.md`
  - Record deferred local-product platform work that should not be implemented in this slice.
- Modify: `src/seektalent/config.py`
  - Add helpers for classifying risky local data roots and evaluating dev/prod policy.
- Modify: `src/seektalent/cli.py`
  - Show local data-root posture in `doctor` and `inspect --json`.
  - Add helper functions for building a safe inspect-time settings snapshot and a data-root posture payload.
- Test: `tests/test_cli.py`
  - Cover inspect/doctor local product fields.
- Test: `tests/test_local_product_contract.py`
  - Cover data-root classification, data-root policy, and local-product docs vocabulary.

## Task 1: Align Public Product Wording

**Files:**

- Modify: `README.md`
- Modify: `docs/ui.md`
- Modify: `docs/cli.md`
- Modify: `docs/configuration.md`

- [ ] **Step 1: Update README product shape**

  Replace the old "minimal local web UI is secondary" wording with local product wording:

  ```markdown
  The current product shape is local-first:

  - the CLI remains the stable terminal entrypoint;
  - the local recruiter workbench is the primary browser UI for business workflows;
  - business data, workbench state, run artifacts, provider snapshots, and backups stay local by default;
  - account entitlement may use a minimal remote control plane, but SeekTalent is not a hosted recruiting SaaS.
  ```

- [ ] **Step 2: Update docs/ui.md startup wording**

  Add a short "Product Boundary" section near the top:

  ```markdown
  ## Product Boundary

  The workbench is a first-class local product surface. Source-checkout developers start the backend and frontend separately; packaged users should eventually get one local startup command. Business users opening the LAN UI do not install Bun, Playwright, Node.js, or DokoBot directly.
  ```

- [ ] **Step 3: Update docs/cli.md**

  Add a "Local Product Entrypoints" section that lists `seektalent` and `seektalent-ui-api` as current commands. Document `seektalent workbench` only under a clearly labeled "target packaged launcher" paragraph so users do not think it exists before implementation.

- [ ] **Step 4: Update docs/configuration.md**

  Add a table for local data roots:

  ```markdown
  | Setting | Purpose |
  | --- | --- |
  | `SEEKTALENT_WORKSPACE_ROOT` | Base for local workbench state when provided. |
  | `SEEKTALENT_ARTIFACTS_DIR` | Artifact root. Relative paths resolve from the workspace root. |
  | `SEEKTALENT_RUNS_DIR` | Legacy run output root for CLI compatibility. |
  ```

- [ ] **Step 5: Add docs vocabulary guard tests**

  Add these tests to `tests/test_local_product_contract.py`:

  ```python
  from pathlib import Path


  PROJECT_ROOT = Path(__file__).resolve().parents[1]
  LOCAL_PRODUCT_DOCS = (
      "README.md",
      "docs/ui.md",
      "docs/cli.md",
      "docs/configuration.md",
  )


  def _local_product_docs_text() -> str:
      return "\n".join((PROJECT_ROOT / path).read_text(encoding="utf-8") for path in LOCAL_PRODUCT_DOCS)


  def test_local_product_docs_use_required_vocabulary() -> None:
      docs = _local_product_docs_text().lower()

      for phrase in (
          "local-first",
          "local recruiter workbench",
          "cli",
          "local workbench",
          "not a hosted recruiting saas",
      ):
          assert phrase in docs


  def test_local_product_docs_reject_old_product_framing() -> None:
      docs = _local_product_docs_text().lower()

      for phrase in (
          "minimal local web ui is secondary",
          "throwaway debug surface",
          "hosted recruiting saas dashboard",
      ):
          assert phrase not in docs
  ```

- [ ] **Step 6: Run docs diff check and docs tests**

  Run:

  ```bash
  git diff --check -- README.md docs/ui.md docs/cli.md docs/configuration.md
  uv run pytest tests/test_local_product_contract.py -q
  ```

  Expected: no whitespace errors. The focused test run passes after the docs use the required local-product wording.

## Task 2: Add Data-Root Safety Classification

**Files:**

- Modify: `src/seektalent/config.py`
- Test: `tests/test_local_product_contract.py`

- [ ] **Step 1: Write failing tests**

  Extend `tests/test_local_product_contract.py`:

  ```python
  from pathlib import Path

  from seektalent.config import classify_local_data_root, evaluate_local_data_root_policy


  def test_repo_root_is_risky_data_root(tmp_path: Path) -> None:
      marker = tmp_path / "pyproject.toml"
      marker.write_text("[project]\nname='seektalent'\n")

      posture = classify_local_data_root(tmp_path)

      assert posture.status == "risky"
      assert posture.reason_code == "repo_root"


  def test_child_of_repo_root_is_risky_data_root(tmp_path: Path) -> None:
      marker = tmp_path / "pyproject.toml"
      marker.write_text("[project]\nname='seektalent'\n")
      data_root = tmp_path / ".seektalent"
      data_root.mkdir()

      posture = classify_local_data_root(data_root)

      assert posture.status == "risky"
      assert posture.reason_code == "inside_repo"


  def test_home_seektalent_data_root_is_safe() -> None:
      posture = classify_local_data_root(Path.home() / ".seektalent")

      assert posture.status == "safe"
      assert posture.reason_code == "user_data_root"


  def test_child_of_home_seektalent_data_root_is_safe() -> None:
      posture = classify_local_data_root(Path.home() / ".seektalent" / "artifacts")

      assert posture.status == "safe"
      assert posture.reason_code == "user_data_root"


  def test_custom_dot_seektalent_root_is_unknown_not_safe(tmp_path: Path) -> None:
      data_root = tmp_path / ".seektalent"
      data_root.mkdir()

      posture = classify_local_data_root(data_root)

      assert posture.status == "unknown"
      assert posture.reason_code == "custom_path"


  def test_sync_folder_data_root_is_risky(tmp_path: Path) -> None:
      data_root = tmp_path / "Dropbox" / ".seektalent"
      data_root.mkdir(parents=True)

      posture = classify_local_data_root(data_root)

      assert posture.status == "risky"
      assert posture.reason_code == "sync_folder"


  def test_company_onedrive_variant_is_risky(tmp_path: Path) -> None:
      data_root = tmp_path / "OneDrive - Company" / ".seektalent"
      data_root.mkdir(parents=True)

      posture = classify_local_data_root(data_root)

      assert posture.status == "risky"
      assert posture.reason_code == "sync_folder"


  def test_sync_folder_classifier_does_not_match_substrings(tmp_path: Path) -> None:
      data_root = tmp_path / "boxcar" / ".seektalent"
      data_root.mkdir(parents=True)

      posture = classify_local_data_root(data_root)

      assert posture.status == "unknown"
      assert posture.reason_code == "custom_path"


  def test_repo_data_root_is_dev_warning(tmp_path: Path) -> None:
      marker = tmp_path / "pyproject.toml"
      marker.write_text("[project]\nname='seektalent'\n")

      policy = evaluate_local_data_root_policy(tmp_path, runtime_mode="dev", packaged=False)

      assert policy.status == "warning"
      assert policy.reason_code == "repo_root"


  def test_repo_data_root_is_prod_error(tmp_path: Path) -> None:
      marker = tmp_path / "pyproject.toml"
      marker.write_text("[project]\nname='seektalent'\n")

      policy = evaluate_local_data_root_policy(tmp_path, runtime_mode="prod", packaged=False)

      assert policy.status == "error"
      assert policy.reason_code == "repo_root"


  def test_repo_data_root_is_packaged_error(tmp_path: Path) -> None:
      marker = tmp_path / "pyproject.toml"
      marker.write_text("[project]\nname='seektalent'\n")

      policy = evaluate_local_data_root_policy(tmp_path, runtime_mode="dev", packaged=True)

      assert policy.status == "error"
      assert policy.reason_code == "repo_root"


  def test_home_data_root_policy_is_safe() -> None:
      policy = evaluate_local_data_root_policy(Path.home() / ".seektalent", runtime_mode="prod", packaged=True)

      assert policy.status == "safe"
      assert policy.reason_code == "user_data_root"
  ```

- [ ] **Step 2: Run failing test**

  Run:

  ```bash
  uv run pytest tests/test_local_product_contract.py -q
  ```

  Expected: import failure for `classify_local_data_root` or `evaluate_local_data_root_policy`.

- [ ] **Step 3: Implement helper**

  Add to `src/seektalent/config.py`:

  ```python
  from dataclasses import dataclass


  @dataclass(frozen=True)
  class LocalDataRootPosture:
      status: Literal["safe", "risky", "unknown"]
      reason_code: str
      path: Path


  @dataclass(frozen=True)
  class LocalDataRootPolicy:
      status: Literal["safe", "warning", "error", "unknown"]
      reason_code: str
      posture: LocalDataRootPosture


  def classify_local_data_root(path: Path) -> LocalDataRootPosture:
      resolved = path.expanduser().resolve(strict=False)
      if (resolved / "pyproject.toml").exists() or (resolved / ".git").exists():
          return LocalDataRootPosture(status="risky", reason_code="repo_root", path=resolved)
      for parent in resolved.parents:
          if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
              return LocalDataRootPosture(status="risky", reason_code="inside_repo", path=resolved)
      normalized_parts = tuple(part.lower() for part in resolved.parts)
      if any(_is_known_sync_folder_part(part) for part in normalized_parts):
          return LocalDataRootPosture(status="risky", reason_code="sync_folder", path=resolved)
      user_data_root = (Path.home() / ".seektalent").resolve(strict=False)
      if resolved == user_data_root or user_data_root in resolved.parents:
          return LocalDataRootPosture(status="safe", reason_code="user_data_root", path=resolved)
      return LocalDataRootPosture(status="unknown", reason_code="custom_path", path=resolved)


  def _is_known_sync_folder_part(part: str) -> bool:
      exact_markers = {
          "icloud drive",
          "mobile documents",
          "dropbox",
          "google drive",
          "googledrive",
          "my drive",
          "box",
          "sharepoint",
          "synology drive",
          "jianguoyun",
          "nutstore",
      }
      return part in exact_markers or part.startswith("onedrive") or part.startswith("one drive")


  def evaluate_local_data_root_policy(
      path: Path,
      *,
      runtime_mode: RuntimeMode,
      packaged: bool = False,
  ) -> LocalDataRootPolicy:
      posture = classify_local_data_root(path)
      if posture.status == "safe":
          return LocalDataRootPolicy(status="safe", reason_code=posture.reason_code, posture=posture)
      if posture.status == "risky":
          status: Literal["warning", "error"] = "error" if runtime_mode == "prod" or packaged else "warning"
          return LocalDataRootPolicy(status=status, reason_code=posture.reason_code, posture=posture)
      return LocalDataRootPolicy(status="unknown", reason_code=posture.reason_code, posture=posture)
  ```

- [ ] **Step 4: Run tests**

  Run:

  ```bash
  uv run pytest tests/test_local_product_contract.py -q
  ```

  Expected: pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/seektalent/config.py tests/test_local_product_contract.py
  git commit -m "feat: classify local data root posture"
  ```

## Task 3: Surface Local Product Contract In CLI

**Files:**

- Modify: `src/seektalent/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add inspect tests**

  Add assertions to the existing `inspect --json` test:

  ```python
  local_product = payload["local_product"]
  assert local_product["contract_version"] == "local-product-contract-v1"
  assert local_product["entrypoints"] == ["cli", "local_workbench"]
  assert local_product["data_root_posture"]["overall_status"] in {"safe", "warning", "error", "unknown"}

  root_names = set(local_product["data_root_posture"]["roots"])
  assert {
      "artifacts",
      "legacy_runs",
      "llm_cache",
      "flywheel_db",
      "corpus_db",
      "workbench_db",
      "liepin_connector_db",
      "liepin_session_store",
      "workbench_backups",
      "browser_session_metadata",
      "logs",
  } <= root_names
  for root_payload in local_product["data_root_posture"]["roots"].values():
      assert {"kind", "status", "reason_code", "path", "exists", "writable"} <= set(root_payload)
  ```

  Add a separate non-leakage test:

  ```python
  def test_inspect_json_does_not_leak_provider_secrets(
      monkeypatch: pytest.MonkeyPatch,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      secret_values = {
          "SEEKTALENT_TEXT_LLM_API_KEY": "secret-text-key-123",
          "SEEKTALENT_CTS_TENANT_KEY": "secret-cts-key-123",
          "SEEKTALENT_CTS_TENANT_SECRET": "secret-cts-secret-123",
          "SEEKTALENT_LIEPIN_API_TOKEN": "secret-liepin-token-123",
          "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "secret-binding-secret-123",
          "SEEKTALENT_LIEPIN_STREAM_TOKEN_SECRET": "secret-stream-secret-123",
      }
      for key, value in secret_values.items():
          monkeypatch.setenv(key, value)

      assert main(["inspect", "--json"]) == 0

      output = capsys.readouterr().out
      for value in secret_values.values():
          assert value not in output


  def test_inspect_json_reports_roots_when_full_settings_is_invalid(
      monkeypatch: pytest.MonkeyPatch,
      tmp_path: Path,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      monkeypatch.chdir(tmp_path)
      (tmp_path / ".env").write_text(
          "SEEKTALENT_REQUIREMENTS_MODEL=openai-chat:legacy\n"
          "SEEKTALENT_WORKSPACE_ROOT=custom-data-root\n",
          encoding="utf-8",
      )

      assert main(["inspect", "--json"]) == 0

      payload = json.loads(capsys.readouterr().out)
      local_product = payload["local_product"]
      assert local_product["settings_source"] == "root_only_fallback"
      assert "custom-data-root" in local_product["data_root_posture"]["roots"]["workbench_db"]["path"]
      assert {
          "artifacts",
          "legacy_runs",
          "llm_cache",
          "flywheel_db",
          "corpus_db",
          "workbench_db",
          "liepin_connector_db",
          "liepin_session_store",
          "workbench_backups",
          "browser_session_metadata",
          "logs",
      } <= set(local_product["data_root_posture"]["roots"])
  ```

- [ ] **Step 2: Add doctor non-leakage test**

  Add this test to `tests/test_cli.py`:

  ```python
  def test_doctor_json_does_not_leak_provider_secrets(
      tmp_path: Path,
      capsys: pytest.CaptureFixture[str],
  ) -> None:
      env_file = tmp_path / ".env"
      secret_values = {
          "SEEKTALENT_TEXT_LLM_API_KEY": "secret-text-key-123",
          "SEEKTALENT_CTS_TENANT_KEY": "secret-cts-key-123",
          "SEEKTALENT_CTS_TENANT_SECRET": "secret-cts-secret-123",
          "SEEKTALENT_LIEPIN_API_TOKEN": "secret-liepin-token-123",
          "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "secret-binding-secret-123",
          "SEEKTALENT_LIEPIN_STREAM_TOKEN_SECRET": "secret-stream-secret-123",
      }
      env_file.write_text(
          "\n".join(f"{key}={value}" for key, value in secret_values.items()) + "\n",
          encoding="utf-8",
      )

      assert main(["doctor", "--env-file", str(env_file), "--output-dir", str(tmp_path / "runs"), "--json"]) == 0

      output = capsys.readouterr().out
      for value in secret_values.values():
          assert value not in output
  ```

- [ ] **Step 3: Run focused test**

  Run:

  ```bash
  uv run pytest tests/test_cli.py -k "inspect or doctor" -q
  ```

  Expected: failure because `local_product` and `local_data_roots` are absent.

- [ ] **Step 4: Add inspect payload**

  In `src/seektalent/cli.py`, add `Callable` to the existing `collections.abc` imports if the file already has that import style; otherwise import it from `typing`. Add `DEV_LLM_CACHE_DIR`, `DEV_RUNS_DIR`, `PROD_LLM_CACHE_DIR`, `PROD_RUNS_DIR`, `RuntimeMode`, `classify_local_data_root`, `evaluate_local_data_root_policy`, and `_packaged_runtime_forces_prod` to the existing `seektalent.config` imports.

  Add helpers like:

  ```python
  def _inspect_local_product_payload() -> dict[str, object]:
      settings, settings_source = _inspect_local_product_settings()
      if settings is None:
          settings_source = "root_only_fallback"
          data_root_posture = _fallback_data_root_posture_payload()
      else:
          data_root_posture = _data_root_posture_payload(settings)
      return {
          "contract_version": "local-product-contract-v1",
          "entrypoints": ["cli", "local_workbench"],
          "default_backend": "seektalent-ui-api",
          "default_frontend": "apps/web",
          "settings_source": settings_source,
          "data_root_posture": data_root_posture,
      }


  def _inspect_local_product_settings() -> tuple[AppSettings | None, str]:
      try:
          return AppSettings(), "default_runtime_settings"
      except Exception:
          return None, "settings_unavailable"


  def _local_product_data_path_builders(settings: AppSettings) -> dict[str, Callable[[], Path]]:
      workbench_root = Path(settings.workspace_root) if settings.workspace_root else settings.project_root
      return {
          "artifacts": lambda: settings.artifacts_path,
          "legacy_runs": lambda: settings.runs_path,
          "llm_cache": lambda: settings.llm_cache_path,
          "flywheel_db": lambda: settings.flywheel_path,
          "corpus_db": lambda: settings.corpus_path,
          "workbench_db": lambda: workbench_root / ".seektalent" / "workbench.sqlite3",
          "liepin_connector_db": lambda: settings.resolve_workspace_path(settings.liepin_connector_db_path),
          "liepin_session_store": lambda: settings.resolve_workspace_path(settings.liepin_session_store_dir),
          "workbench_backups": lambda: workbench_root / ".seektalent" / "backups",
          "browser_session_metadata": lambda: workbench_root / ".seektalent" / "browser_sessions",
          "logs": lambda: workbench_root / ".seektalent" / "logs",
      }


  def _fallback_data_root_posture_payload() -> dict[str, object]:
      workspace_root = _fallback_workspace_root()
      runtime_mode = _fallback_runtime_mode()
      root_kinds = _local_product_root_kinds()
      roots = {
          name: _single_fallback_data_root_payload(
              name=name,
              path=path,
              kind=root_kinds[name],
              runtime_mode=runtime_mode,
          )
          for name, path in _fallback_local_product_data_paths(workspace_root, runtime_mode).items()
      }
      statuses = {str(payload["status"]) for payload in roots.values()}
      if "error" in statuses:
          overall_status = "error"
      elif "warning" in statuses:
          overall_status = "warning"
      elif statuses == {"safe"}:
          overall_status = "safe"
      else:
          overall_status = "unknown"
      return {"overall_status": overall_status, "roots": roots}


  def _fallback_local_product_data_paths(workspace_root: Path, runtime_mode: RuntimeMode) -> dict[str, Path]:
      artifacts_dir = _raw_env_value("SEEKTALENT_ARTIFACTS_DIR", env_file=".env") or (
          PROD_ARTIFACTS_DIR if runtime_mode == "prod" else DEV_ARTIFACTS_DIR
      )
      runs_dir = _raw_env_value("SEEKTALENT_RUNS_DIR", env_file=".env") or (
          PROD_RUNS_DIR if runtime_mode == "prod" else DEV_RUNS_DIR
      )
      llm_cache_dir = _raw_env_value("SEEKTALENT_LLM_CACHE_DIR", env_file=".env") or (
          PROD_LLM_CACHE_DIR if runtime_mode == "prod" else DEV_LLM_CACHE_DIR
      )
      flywheel_db = _raw_env_value("SEEKTALENT_FLYWHEEL_DB_PATH", env_file=".env") or ".seektalent/flywheel.sqlite3"
      corpus_db = _raw_env_value("SEEKTALENT_CORPUS_DB_PATH", env_file=".env") or ".seektalent/corpus.sqlite3"
      liepin_db = (
          _raw_env_value("SEEKTALENT_LIEPIN_CONNECTOR_DB_PATH", env_file=".env")
          or ".seektalent/liepin_connector.sqlite3"
      )
      liepin_sessions = (
          _raw_env_value("SEEKTALENT_LIEPIN_SESSION_STORE_DIR", env_file=".env") or ".seektalent/liepin_sessions"
      )
      workbench_root = _fallback_workspace_root()
      return {
          "artifacts": _fallback_resolve_workspace_path(artifacts_dir, workspace_root),
          "legacy_runs": _fallback_resolve_workspace_path(runs_dir, workspace_root),
          "llm_cache": _fallback_resolve_workspace_path(llm_cache_dir, workspace_root),
          "flywheel_db": _fallback_resolve_workspace_path(flywheel_db, workspace_root),
          "corpus_db": _fallback_resolve_workspace_path(corpus_db, workspace_root),
          "workbench_db": workbench_root / ".seektalent" / "workbench.sqlite3",
          "liepin_connector_db": _fallback_resolve_workspace_path(liepin_db, workspace_root),
          "liepin_session_store": _fallback_resolve_workspace_path(liepin_sessions, workspace_root),
          "workbench_backups": workbench_root / ".seektalent" / "backups",
          "browser_session_metadata": workbench_root / ".seektalent" / "browser_sessions",
          "logs": workbench_root / ".seektalent" / "logs",
      }


  def _fallback_runtime_mode() -> RuntimeMode:
      return "prod" if _raw_env_value("SEEKTALENT_RUNTIME_MODE", env_file=".env") == "prod" else "dev"


  def _fallback_workspace_root() -> Path:
      return _fallback_resolve_workspace_path(_raw_env_value("SEEKTALENT_WORKSPACE_ROOT", env_file=".env") or ".", Path.cwd())


  def _fallback_resolve_workspace_path(value: str, root: Path) -> Path:
      path = Path(value).expanduser()
      if path.is_absolute():
          return path
      return root / path


  def _local_product_root_kinds() -> dict[str, str]:
      return {
          "artifacts": "directory",
          "legacy_runs": "directory",
          "llm_cache": "cache",
          "flywheel_db": "sqlite",
          "corpus_db": "sqlite",
          "workbench_db": "sqlite",
          "liepin_connector_db": "sqlite",
          "liepin_session_store": "session_store",
          "workbench_backups": "backup",
          "browser_session_metadata": "session_store",
          "logs": "log",
      }


  def _data_root_posture_payload(settings: AppSettings | None) -> dict[str, object]:
      if settings is None:
          return _fallback_data_root_posture_payload()
      root_kinds = _local_product_root_kinds()
      roots = {
          name: _single_data_root_payload(
              name=name,
              build_path=build_path,
              kind=root_kinds[name],
              settings=settings,
          )
          for name, build_path in _local_product_data_path_builders(settings).items()
      }
      statuses = {str(payload["status"]) for payload in roots.values()}
      if "error" in statuses:
          overall_status = "error"
      elif "warning" in statuses:
          overall_status = "warning"
      elif statuses == {"safe"}:
          overall_status = "safe"
      else:
          overall_status = "unknown"
      return {"overall_status": overall_status, "roots": roots}


  def _single_data_root_payload(
      *,
      name: str,
      build_path: Callable[[], Path],
      kind: str,
      settings: AppSettings,
  ) -> dict[str, object]:
      del name
      try:
          path = build_path()
      except Exception:
          return {
              "kind": kind,
              "status": "unknown",
              "reason_code": "path_unavailable",
              "exists": False,
              "writable": False,
          }
      policy = evaluate_local_data_root_policy(
          path,
          runtime_mode=settings.runtime_mode,
          packaged=_packaged_runtime_forces_prod(),
      )
      return {
          "kind": kind,
          "status": policy.status,
          "reason_code": policy.reason_code,
          "path": str(policy.posture.path),
          "exists": policy.posture.path.exists(),
          "writable": _local_product_path_writable(policy.posture.path),
      }


  def _single_fallback_data_root_payload(
      *,
      name: str,
      path: Path,
      kind: str,
      runtime_mode: RuntimeMode,
  ) -> dict[str, object]:
      del name
      policy = evaluate_local_data_root_policy(
          path,
          runtime_mode=runtime_mode,
          packaged=_packaged_runtime_forces_prod(),
      )
      return {
          "kind": kind,
          "status": policy.status,
          "reason_code": policy.reason_code,
          "path": str(policy.posture.path),
          "exists": policy.posture.path.exists(),
          "writable": _local_product_path_writable(policy.posture.path),
      }


  def _local_product_path_writable(path: Path) -> bool:
      target = path if path.exists() and path.is_dir() else path.parent
      return target.exists() and os.access(target, os.W_OK)
  ```

  Then add the result to `_inspect_payload()`:

  ```python
  "local_product": _inspect_local_product_payload(),
  ```

  The helper must not print secrets, read candidate artifacts, or fail the whole inspect command because local settings are invalid. It must not use `_env_file=None`, because that makes inspect disagree with the normal runtime `.env` behavior.

- [ ] **Step 5: Add doctor check**

  Add a doctor check named `local_data_roots` that reports the overall safe/warning/error/unknown posture and the reason codes for each local data path using the already-built doctor settings.

  ```python
  def _local_data_roots_check(settings: AppSettings | None) -> DoctorCheck:
      assert settings is not None
      posture = _data_root_posture_payload(settings)
      roots = posture["roots"]
      assert isinstance(roots, dict)
      root_summaries = [
          f"{name}={payload['status']}:{payload['reason_code']}"
          for name, payload in roots.items()
          if isinstance(payload, dict)
      ]
      overall_status = str(posture["overall_status"])
      ok = overall_status != "error"
      return DoctorCheck(
          "local_data_roots",
          ok,
          f"Local data roots posture={overall_status}; " + ", ".join(root_summaries),
      )
  ```

  Append this check after the settings-dependent checks in `_doctor_command()`. It must not print secrets or scan candidate data. If settings construction failed, skip this check because the existing `settings` check already reports the config error.

- [ ] **Step 6: Run verification**

  ```bash
  uv run pytest tests/test_cli.py tests/test_local_product_contract.py -q
  uv run ruff check src/seektalent/cli.py src/seektalent/config.py tests/test_cli.py tests/test_local_product_contract.py
  ```

  Expected: pass.

- [ ] **Step 7: Commit**

  ```bash
  git add src/seektalent/cli.py src/seektalent/config.py tests/test_cli.py tests/test_local_product_contract.py
  git commit -m "feat: expose local product contract"
  ```

## Task 4: Record Deferred Local Product Platform Work

**Files:**

- Modify: `TODOS.md`

- [ ] **Step 1: Ensure deferred local-product section exists once**

  Add this section under `## Infrastructure` if it is missing. If it already exists, update that section in place; do not create a duplicate heading.

  ```markdown
  ### Local Product Platform Follow-Ups

  **What:** Split the larger local-product platform work into later plans instead of adding it to the first local product contract slice.

  **Why:** The current local product contract should establish wording, data-root posture, inspect/doctor safety, and non-leakage checks. Full storage, security posture, schema, installer, connector, entitlement, and launcher work is broader and should be planned separately.

  **Deferred items:**

  - Complete SQLite lifecycle: WAL policy, busy timeout, migration locking, checkpointing, and cross-database backup/restore. The current workbench already has a SQLite backup path, so this should become a focused local storage reliability plan rather than blocking the first contract slice.
  - Local web security posture expansion: Host/Origin/CSRF risks are real, but `network_guard.py` and `tests/test_workbench_network_guard.py` already cover the core guard. Later work should surface network posture in inspect/doctor instead of rewriting the guard here.
  - JSON Schema / OpenAPI contracts: add `contract_version` and field tests now; full schema files and OpenAPI contract tests should be a later compatibility plan.
  - Platform and packaging expansion: platform-specific user data directories, provider connector posture plugins, entitlement leases/offline grace, and the complete `seektalent workbench` launcher argument/output contract belong to later productization or packaging plans.

  **Effort:** L
  **Priority:** P1
  **Depends on:** Local product contract build, entitlement/key-control plan, and packaging direction.
  ```

- [ ] **Step 2: Run docs diff and duplicate-section checks**

  Run:

  ```bash
  git diff --check -- TODOS.md
  test "$(rg -n '^### Local Product Platform Follow-Ups$' TODOS.md | wc -l | tr -d ' ')" = "1"
  ```

  Expected: no whitespace errors and exactly one `Local Product Platform Follow-Ups` section.

- [ ] **Step 3: Commit**

  ```bash
  git add TODOS.md
  git commit -m "docs: record local product follow-up plans"
  ```

## Self-Review

- Spec coverage: product wording, data root safety, dev/prod data-root policy, CLI/doctor/inspect contract, non-leakage checks, docs alignment, and deferred follow-up recording are covered.
- Placeholder scan: no step uses unspecified implementation work.
- Type consistency: `LocalDataRootPosture`, `LocalDataRootPolicy`, `classify_local_data_root`, and `evaluate_local_data_root_policy` are introduced before use.
