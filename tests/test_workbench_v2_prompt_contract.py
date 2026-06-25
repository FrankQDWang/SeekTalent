from pathlib import Path


PROMPT = Path("src/seektalent_workbench_v2/prompts/system.md")


def test_system_prompt_requires_intent_classification_before_action() -> None:
    text = PROMPT.read_text(encoding="utf-8")

    assert "Classify every user turn before taking action." in text
    assert "Do not assume arbitrary text is a JD." in text
    assert "Pure chat" in text
    assert "progress question" in text
    assert "supplementary requirement" in text
    assert "jobTitle" in text
    assert "jd" in text
    assert "notes" in text
    assert "Never start runtime when jobTitle or jd is missing." in text
