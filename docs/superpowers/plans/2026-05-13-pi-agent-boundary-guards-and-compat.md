# PI Agent Boundary Guards And Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce PI Agent browser-boundary rules and keep the legacy Liepin worker boundary aligned while DokoBot action mode remains capability-gated.

**Architecture:** The Python scanner blocks direct authenticated API replay patterns, DokoBot/DevTools network inspection, and arbitrary in-page script evaluation in PI Agent and Liepin browser-automation code. The existing Bun worker boundary check stays aligned with the same forbidden pattern list until legacy worker compatibility is removed.

**Tech Stack:** Python 3.12, pytest, Bun, existing `apps/liepin-worker` boundary check.

**Spec:** `docs/superpowers/specs/2026-05-13-provider-interaction-agent-dokobot-design.md`

**Depends On:**
- `docs/superpowers/plans/2026-05-13-pi-agent-contracts-and-skill-recipes.md`
- `docs/superpowers/plans/2026-05-13-dokobot-capability-and-protected-artifacts.md`
- `docs/superpowers/plans/2026-05-13-detail-grants-and-backend-dispatch.md`

---

## File Structure

- Add: `tools/check_pi_agent_boundaries.py`
  - Static scanner for direct authenticated API replay patterns.
- Test: `tests/test_pi_agent_boundaries.py`
  - Scanner unit tests.
- Modify: `apps/liepin-worker/scripts/checkBoundaries.ts`
  - Keep the Bun boundary check aligned with the PI Agent forbidden direct-request pattern list while legacy worker compatibility remains active.

### Task 1: Add Boundary Scan And Compatibility Verification

**Files:**
- Create: `tools/check_pi_agent_boundaries.py`
- Test: `tests/test_pi_agent_boundaries.py`
- Modify: `apps/liepin-worker/scripts/checkBoundaries.ts`

- [ ] **Step 1: Write failing static-boundary tests**

Add `tests/test_pi_agent_boundaries.py`:

```python
from tools.check_pi_agent_boundaries import find_forbidden_direct_request_patterns


def test_boundary_scan_finds_playwright_request_context_usage() -> None:
    files = {
        "src/seektalent/providers/liepin/example.ts": "await page.request.get('/api')",
        "src/seektalent/providers/pi_agent/example.py": "safe_action()",
    }

    findings = find_forbidden_direct_request_patterns(files)

    assert findings == [("src/seektalent/providers/liepin/example.ts", "page.request")]


def test_boundary_scan_finds_dokobot_devtools_request_and_evaluate_usage() -> None:
    files = {
        "src/seektalent/providers/pi_agent/example.py": "tools.list_network_requests(); tools.evaluate_script('document.cookie')",
    }

    findings = find_forbidden_direct_request_patterns(files)

    assert findings == [
        ("src/seektalent/providers/pi_agent/example.py", "list_network_requests"),
        ("src/seektalent/providers/pi_agent/example.py", "evaluate_script"),
    ]


def test_boundary_scan_allows_safe_page_actions() -> None:
    files = {
        "src/seektalent/providers/liepin/example.ts": "await page.getByText('Next').click()",
    }

    assert find_forbidden_direct_request_patterns(files) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_pi_agent_boundaries.py -q
```

Expected: import failure for `tools.check_pi_agent_boundaries`.

- [ ] **Step 3: Implement scanner**

Add `tools/check_pi_agent_boundaries.py`:

```python
from pathlib import Path


FORBIDDEN_DIRECT_REQUEST_PATTERNS = (
    "page.request",
    "browserContext.request",
    "APIRequestContext",
    "list_network_requests",
    "get_network_request",
    "evaluate_script",
)


def find_forbidden_direct_request_patterns(files: dict[str, str]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for path, text in files.items():
        for pattern in FORBIDDEN_DIRECT_REQUEST_PATTERNS:
            if pattern in text:
                findings.append((path, pattern))
    return findings


def main() -> int:
    roots = (
        Path("src/seektalent/providers/pi_agent"),
        Path("src/seektalent/providers/liepin"),
        Path("apps/liepin-worker/src"),
    )
    files: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ts", ".tsx"}:
                files[str(path)] = path.read_text(encoding="utf-8")
    findings = find_forbidden_direct_request_patterns(files)
    for path, pattern in findings:
        print(f"{path}: forbidden direct provider request pattern {pattern}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Align the Bun worker boundary check**

Open `apps/liepin-worker/scripts/checkBoundaries.ts` and make sure the same direct-request, network-inspection, and arbitrary-evaluate patterns are blocked:

```ts
const forbiddenDirectRequestPatterns = [
  "page.request",
  "browserContext.request",
  "APIRequestContext",
  "list_network_requests",
  "get_network_request",
  "evaluate_script",
];
```

Keep existing worker-specific checks in that file. The change here is to make the direct authenticated API replay rules match the Python PI scanner.

- [ ] **Step 5: Run full PI verification**

```bash
uv run pytest tests/test_pi_agent_contracts.py tests/test_liepin_pi_skills.py tests/test_dokobot_capabilities.py tests/test_liepin_detail_policy.py tests/test_pi_agent_artifacts.py tests/test_pi_agent_boundaries.py tests/test_liepin_provider_adapter.py -q
uv run python tools/check_pi_agent_boundaries.py
cd apps/liepin-worker && bun run boundary-check
```

Expected: pass.

- [ ] **Step 6: Commit boundary guardrails**

```bash
git add tools/check_pi_agent_boundaries.py tests/test_pi_agent_boundaries.py apps/liepin-worker/scripts/checkBoundaries.ts
git commit -m "test: enforce pi agent browser boundaries"
```

## Self-Review

- Spec coverage: direct authenticated API replay guardrails and legacy worker compatibility verification are covered.
- Placeholder scan: every step names concrete files, tests, commands, and expected outcomes.
- Type consistency: this plan consumes outputs from the earlier contract, DokoBot, and runner plans without redefining their models.
