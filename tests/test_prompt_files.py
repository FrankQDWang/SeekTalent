from __future__ import annotations

from pathlib import Path


def _prompt_text(name: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (
        repo_root / "src" / "seektalent" / "prompts" / name
    ).read_text(encoding="utf-8")


def test_requirement_extraction_prompt_contains_field_rules_and_examples() -> None:
    text = _prompt_text("bootstrap_requirement_extraction.md")

    assert "## 字段边界" in text
    assert "## 冲突处理" in text
    assert "## 示例" in text
    assert "must_have_capability_candidates" in text


def test_controller_prompt_contains_rubric_and_examples() -> None:
    text = _prompt_text("search_controller_decision.md")

    assert "## 决策步骤" in text
    assert "## Operator 选择规则" in text
    assert "## 示例" in text
    assert "crossover_compose" in text


def test_branch_evaluation_prompt_contains_scoring_rubric_and_examples() -> None:
    text = _prompt_text("branch_outcome_evaluation.md")

    assert "## Scoring Rubric" in text
    assert "branch_exhausted" in text
    assert "## Examples" in text
    assert "repair_operator_hint" in text


def test_finalization_prompt_contains_summary_contract_and_examples() -> None:
    text = _prompt_text("search_run_finalization.md")

    assert "## Summary Contract" in text
    assert "## Style Contract" in text
    assert "## Examples" in text
    assert "run_summary" in text


def test_prompt_readme_explains_instruction_and_surface_split() -> None:
    text = _prompt_text("README.md")

    assert "Complete prompt behavior is split across two layers" in text
    assert "few-shot examples in the `.md` instruction prompt" in text
