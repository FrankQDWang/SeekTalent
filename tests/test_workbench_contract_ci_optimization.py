from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_playwright_configs_pin_ci_workers():
    for relative_path in (
        "apps/web-react/playwright.config.ts",
        "apps/web-react/playwright.storybook.config.ts",
        "apps/web-react/playwright.storybook-visual.config.ts",
    ):
        config = (ROOT / relative_path).read_text(encoding="utf-8")

        assert "workers: process.env.CI ? 4 : undefined" in config, relative_path


def test_workbench_contract_ci_skips_duplicate_python_preflight():
    workflow = (ROOT / ".github/workflows/workbench-contract.yml").read_text(encoding="utf-8")

    assert 'SEEKTALENT_VERIFY_SKIP_PYTHON_PREFLIGHT: "1"' in workflow


def test_verify_workbench_supports_python_preflight_skip_mode():
    script = (ROOT / "scripts" / "verify-dev-workbench.sh").read_text(encoding="utf-8")

    assert "SEEKTALENT_VERIFY_SKIP_PYTHON_PREFLIGHT" in script


def test_verify_workbench_forbidden_copy_gate_does_not_require_rg():
    script = (ROOT / "scripts" / "verify-dev-workbench.sh").read_text(encoding="utf-8")

    assert "rg -n -i" not in script
    assert "git grep --untracked -n -i -F" in script
