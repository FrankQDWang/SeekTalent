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


def test_workbench_contract_ci_does_not_use_slow_pnpm_cache_post_step():
    workflow = (ROOT / ".github/workflows/workbench-contract.yml").read_text(encoding="utf-8")

    assert "pnpm/action-setup" not in workflow
    assert 'cache: "pnpm"' not in workflow
    assert "corepack enable && corepack prepare pnpm@11.6.0 --activate" in workflow


def test_python_quality_uses_slim_direct_main_gate():
    workflow = (ROOT / ".github/workflows/python-quality.yml").read_text(encoding="utf-8")

    assert "tools/check_arch_imports.py" in workflow
    assert "tools/check_tach_baseline.py" not in workflow
    assert "tools/check_privacy_gate.py --base" in workflow
    assert "tools/check_agent_safety_gate.py --base" in workflow


def test_verify_workbench_supports_python_preflight_skip_mode():
    script = (ROOT / "scripts" / "verify-dev-workbench.sh").read_text(encoding="utf-8")

    assert "SEEKTALENT_VERIFY_SKIP_PYTHON_PREFLIGHT" in script


def test_storybook_contract_runs_against_static_ci_build():
    script = (ROOT / "scripts" / "verify-dev-workbench.sh").read_text(encoding="utf-8")

    assert "PNPM_CMD=(corepack pnpm)" in script
    assert '"${PNPM_CMD[@]}" storybook:build --test --quiet --disable-telemetry' in script
    assert "python3 -m http.server 6006" in script
    assert "curl -fsS \"http://127.0.0.1:6006/iframe.html\"" in script
    assert 'SEEKTALENT_STORYBOOK_EXTERNAL=1 "${PNPM_CMD[@]}" storybook:a11y' in script
    assert 'SEEKTALENT_STORYBOOK_EXTERNAL=1 "${PNPM_CMD[@]}" storybook:interactions' in script
    assert 'SEEKTALENT_STORYBOOK_EXTERNAL=1 "${PNPM_CMD[@]}" storybook:visual' in script

    for relative_path in (
        "apps/web-react/playwright.storybook.config.ts",
        "apps/web-react/playwright.storybook-visual.config.ts",
    ):
        config = (ROOT / relative_path).read_text(encoding="utf-8")

        assert "SEEKTALENT_STORYBOOK_STATIC" in config, relative_path
        assert "SEEKTALENT_STORYBOOK_EXTERNAL" in config, relative_path
        assert "python3 -m http.server 6006" in config, relative_path
        assert "storybook-static" in config, relative_path
        assert "corepack pnpm exec storybook dev" in config, relative_path
        assert "...storybookWebServer" in config, relative_path


def test_verify_workbench_forbidden_copy_gate_does_not_require_rg():
    script = (ROOT / "scripts" / "verify-dev-workbench.sh").read_text(encoding="utf-8")

    assert "rg -n -i" not in script
    assert "git grep --untracked -n -i -F" in script
