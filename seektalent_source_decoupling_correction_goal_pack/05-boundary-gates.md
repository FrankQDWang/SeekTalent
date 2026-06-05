# Boundary Gate Requirements

## Source Boundary Checker

`tools/check_source_boundaries.py` must be strengthened to catch the original
missed violations.

It must inspect Python AST where practical and text where needed.

This section is intentionally implementation-specific. The previous goal failed
because the checker described a broad invariant but implemented only a narrow
subset.

## Runtime Forbidden Imports

Runtime must not import:

- `seektalent.providers`
- `seektalent.clients.cts_client`
- concrete `seektalent.sources.cts` modules
- concrete `seektalent.sources.liepin` modules
- `seektalent.source_adapters` modules

Allowed runtime source imports should be limited to neutral contracts/registry
modules, for example:

- `seektalent.sources.contracts`
- `seektalent.sources.registry`
- `seektalent.sources.public_events` if it remains source-neutral
- `seektalent.source_contracts.*` after the package split

If a module under `seektalent.sources` imports providers or runtime, it is not
source-neutral and runtime must not import it.

Required checker constant:

```python
FORBIDDEN_RUNTIME_IMPORTS = (
    "seektalent.providers",
    "seektalent.clients.cts_client",
    "seektalent.sources.cts",
    "seektalent.sources.liepin",
    "seektalent.source_adapters",
)
```

If the migration deletes `seektalent.sources.cts/liepin`, keep the forbidden
entries anyway so the checker catches regressions.

## Runtime Forbidden Source Branches

The checker must catch at least these forms in runtime production code:

```python
if source == "cts": ...
if source != "liepin": ...
if source in {"cts", "liepin"}: ...
if source not in {"cts", "liepin"}: ...
match source:
    case "cts": ...
{"cts": cts_runner, "liepin": liepin_runner}
```

The checker should avoid flagging documentation strings or tests unless those
tests are intentionally asserting forbidden examples. If the implementation uses
AST, include tests for compare, set membership, dict literal dispatch, and match.

Required AST behavior:

```python
CONCRETE_SOURCE_IDS = {"cts", "liepin"}

def _string_literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

def _string_set(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return {value for item in node.elts if (value := _string_literal(item))}
    return set()

def _name_contains_source(value: str) -> bool:
    lowered = value.lower()
    return "source" in lowered or "provider_name" in lowered

def _expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _expr_name(node.value)
    return ""
```

The checker must flag:

- `ast.Compare` where either side contains a concrete source id and the compared
  expression name contains `source` or `provider_name`, for `Eq`, `NotEq`, `In`,
  and `NotIn`.
- `ast.Dict` with any key in `{"cts", "liepin"}` when the value is callable-like
  dispatch (`ast.Name`, `ast.Attribute`, `ast.Lambda`) or the dict is assigned to
  a name containing `runner`, `adapter`, `builder`, `dispatch`, or `source`.
- `ast.Match` cases matching `"cts"` or `"liepin"`.
- calls to `.get("cts")`, `.get("liepin")`, indexing `mapping["cts"]`, or
  `mapping["liepin"]` in runtime dispatch code, unless the checker has a narrow
  allowlist for test fixtures or public serialization of already-generic data.

Required messages should be distinct so the red evidence is unambiguous:

```text
runtime must not import concrete source implementation
runtime must not compare against concrete source ids
runtime must not dispatch through concrete source id maps
runtime must not index source plans by concrete source id
```

## Runtime Forbidden Provider Leakage

The checker must catch runtime production code containing:

- `liepin_opencli`
- `opencli` when used as provider/backend behavior, not unrelated docs;
- `RuntimeApprovedDetailLease currently supports only liepin`;
- runtime budget dataclass fields prefixed with `cts_` or `liepin_`;
- runtime functions named `_run_cts_*`, `_run_liepin_*`, or equivalent concrete
  source execution paths.
- defaults such as `("cts", "liepin")` in runtime source selection.

If any exception is necessary, it must be documented in the checker with a narrow
allowlist and a test explaining why it is source-neutral.

Required AST/text behavior:

- flag `ast.FunctionDef` and `ast.AsyncFunctionDef` names containing `_cts_` or
  `_liepin_` under `src/seektalent/runtime`;
- flag `ast.AnnAssign` and `ast.Assign` field names inside runtime dataclasses
  when the target starts with `cts_` or `liepin_`;
- flag string constants containing `liepin_opencli`;
- flag `opencli` in runtime except in test-only negative examples;
- flag tuple/list/set constants exactly containing both `"cts"` and `"liepin"`
  when used as default source selection.

`opencli` exceptions are allowed only in checker tests under `tests/` when the
string is part of an intentional negative fixture, assertion, or test comment.
No `opencli` exception is allowed under `src/seektalent/runtime/**`.

The checker test suite must include negative examples for:

```python
from seektalent.sources.liepin.runtime_lane import run_liepin_source_lane
from seektalent.sources.cts.filter_projection import project_constraints_to_cts
if source not in {"cts", "liepin"}: ...
if provider_name != "liepin": ...
_SOURCE_LANE_REQUEST_RUNNERS = {"liepin": run_liepin_source_lane}
source_plan_by_source["cts"]
selected_sources = selected or ("cts", "liepin")
class RuntimeSourceBudgetPolicy:
    cts_page_size: int = 10
async def _run_cts_source_lane(...): ...
```

## Tach Requirements

`tach.toml` and `tests/test_tach_baseline.py` must enforce:

- no accepted Tach failures;
- `seektalent.providers` does not depend on `seektalent.runtime`;
- `seektalent.runtime` does not depend on `seektalent.providers`;
- `seektalent.sources` neutral contracts/registry do not depend on
  `seektalent.runtime`;
- no runtime/sources/providers cycle.

If the repository needs separate implementation packages, split neutral source
contracts from concrete source adapters rather than modeling a cycle.

Required Tach test behavior:

- parse `tach.toml` into `dependencies_by_module`;
- assert `seektalent.source_contracts` exists after the split;
- assert `seektalent.runtime` depends on `seektalent.source_contracts`;
- assert `seektalent.runtime` does not depend on `seektalent.sources`,
  `seektalent.source_adapters`, or `seektalent.providers`;
- assert `seektalent.source_contracts` depends on neither
  `seektalent.runtime`, `seektalent.providers`, nor
  `seektalent.source_adapters`;
- assert a graph traversal over declared modules finds no cycle containing any
  of `seektalent.runtime`, `seektalent.source_contracts`,
  `seektalent.source_adapters`, `seektalent.sources`, or
  `seektalent.providers`.

Do not rely only on direct dependency assertions. The previous Tach config passed
because direct runtime/provider checks missed the runtime/sources/providers
cycle.

Do not add Tach cycles to `tools/tach_baseline.json`. Current cycles are red
evidence to record in the progress ledger, not accepted failures.

## Verify Script

`scripts/verify-source-decoupling.sh` must run the hardened checker before
pytest. It must fail if the checker fails.

## Required Red Commands

After adding checker tests but before implementing checker logic, these commands
must fail:

```bash
uv run pytest tests/test_source_boundaries.py::test_runtime_concrete_source_import_is_reported -q
uv run pytest tests/test_source_boundaries.py::test_runtime_source_membership_whitelist_is_reported -q
uv run pytest tests/test_source_boundaries.py::test_runtime_concrete_source_dispatch_map_is_reported -q
uv run pytest tests/test_tach_baseline.py::test_tach_config_has_no_runtime_source_provider_cycle -q
```

After implementing checker logic but before product migration, these commands
must fail against the current product code:

```bash
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
scripts/verify-source-decoupling.sh
```

The failure output must include at least one concrete source import violation,
one concrete source branch/dispatch violation, and one Tach cycle violation. If a
command passes at this stage, the gate is still too weak.
