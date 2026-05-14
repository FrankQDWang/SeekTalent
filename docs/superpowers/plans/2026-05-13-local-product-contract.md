# Local Product Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SeekTalent's local CLI plus local workbench product contract explicit in docs, startup checks, and inspect/doctor output.

**Architecture:** Keep the current source-checkout startup path intact while adding a stable local-product vocabulary and data-root safety checks. The implementation is documentation-first with small CLI/config tests so future packaging and entitlement work has a fixed boundary to target.

**Tech Stack:** Python 3.12, Pydantic settings, argparse CLI, pytest, existing FastAPI workbench docs.

**Spec:** `docs/superpowers/specs/2026-05-13-local-product-contract-design.md`

---

## File Structure

- Modify: `README.md`
  - Describe SeekTalent as a local recruiter workbench with CLI and local UI entrypoints.
- Modify: `docs/ui.md`
  - Keep workbench docs first-class and align setup wording with local product contract.
- Modify: `docs/cli.md`
  - Add local product entrypoint notes and packaged-startup target wording.
- Modify: `docs/configuration.md`
  - Document local data root safety and runtime mode expectations.
- Modify: `src/seektalent/config.py`
  - Add helper for classifying risky local data roots.
- Modify: `src/seektalent/cli.py`
  - Show data-root posture in `doctor` and `inspect --json`.
- Test: `tests/test_cli.py`
  - Cover inspect/doctor local product fields.
- Test: `tests/test_local_product_contract.py`
  - Cover data-root classification helper.

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

- [ ] **Step 5: Run docs diff check**

  Run:

  ```bash
  git diff --check -- README.md docs/ui.md docs/cli.md docs/configuration.md
  ```

  Expected: no whitespace errors.

## Task 2: Add Data-Root Safety Classification

**Files:**

- Modify: `src/seektalent/config.py`
- Test: `tests/test_local_product_contract.py`

- [ ] **Step 1: Write failing tests**

  Add `tests/test_local_product_contract.py`:

  ```python
  from pathlib import Path

  from seektalent.config import classify_local_data_root


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
  ```

- [ ] **Step 2: Run failing test**

  Run:

  ```bash
  uv run pytest tests/test_local_product_contract.py -q
  ```

  Expected: import failure for `classify_local_data_root`.

- [ ] **Step 3: Implement helper**

  Add to `src/seektalent/config.py`:

  ```python
  from dataclasses import dataclass


  @dataclass(frozen=True)
  class LocalDataRootPosture:
      status: Literal["safe", "risky", "unknown"]
      reason_code: str
      path: Path


  def classify_local_data_root(path: Path) -> LocalDataRootPosture:
      resolved = path.expanduser().resolve()
      if (resolved / "pyproject.toml").exists() or (resolved / ".git").exists():
          return LocalDataRootPosture(status="risky", reason_code="repo_root", path=resolved)
      for parent in resolved.parents:
          if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
              return LocalDataRootPosture(status="risky", reason_code="inside_repo", path=resolved)
      parts = {part.lower() for part in resolved.parts}
      if {"icloud drive", "dropbox", "onedrive", "google drive"}.intersection(parts):
          return LocalDataRootPosture(status="risky", reason_code="sync_folder", path=resolved)
      if resolved.name == ".seektalent":
          return LocalDataRootPosture(status="safe", reason_code="user_data_root", path=resolved)
      return LocalDataRootPosture(status="unknown", reason_code="custom_path", path=resolved)
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

- [ ] **Step 1: Add inspect test**

  Add an assertion to the existing `inspect --json` test:

  ```python
  assert payload["local_product"]["entrypoints"] == ["cli", "local_workbench"]
  assert payload["local_product"]["data_root_posture"]["status"] in {"safe", "risky", "unknown"}
  ```

- [ ] **Step 2: Run focused test**

  Run:

  ```bash
  uv run pytest tests/test_cli.py -k inspect -q
  ```

  Expected: failure because `local_product` is absent.

- [ ] **Step 3: Add inspect payload**

  In `src/seektalent/cli.py`, add a `local_product` object to the inspect payload:

  ```python
  "local_product": {
      "entrypoints": ["cli", "local_workbench"],
      "default_backend": "seektalent-ui-api",
      "default_frontend": "apps/web",
      "data_root_posture": _data_root_posture_payload(settings),
  },
  ```

- [ ] **Step 4: Add doctor check**

  Add a doctor check named `local_data_root` that reports safe/risky/unknown and the reason code. It must not print secrets or scan candidate data.

- [ ] **Step 5: Run verification**

  ```bash
  uv run pytest tests/test_cli.py tests/test_local_product_contract.py -q
  uv run ruff check src/seektalent/cli.py src/seektalent/config.py tests/test_cli.py tests/test_local_product_contract.py
  ```

  Expected: pass.

- [ ] **Step 6: Commit**

  ```bash
  git add src/seektalent/cli.py src/seektalent/config.py tests/test_cli.py tests/test_local_product_contract.py
  git commit -m "feat: expose local product contract"
  ```

## Self-Review

- Spec coverage: product wording, data root safety, CLI/doctor/inspect contract, and docs alignment are covered.
- Placeholder scan: no step uses unspecified implementation work.
- Type consistency: `LocalDataRootPosture` and `classify_local_data_root` are introduced before use.
