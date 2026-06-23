from __future__ import annotations

from pathlib import Path


PROMPT_PATH = Path("src/seektalent/prompts/conversation_agent.md")


def test_conversation_agent_prompt_uses_locked_sections() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    headings = [line.removeprefix("## ").strip() for line in prompt.splitlines() if line.startswith("## ")]

    assert headings == [
        "Identity",
        "Operating Principles",
        "Architecture Boundaries",
        "Tool And Action Boundary",
        "Intent Classes",
        "Intent Routing And Service Handoff",
        "Requirement Flow",
        "Runtime Answers",
        "Final Output",
    ]


def test_conversation_agent_prompt_contains_required_contract_phrases() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    for phrase in [
        "You do not directly call tools",
        "host service",
        "service actions",
        "runtime facts are read-only evidence",
        "frontend depends on BFF only",
        "workflow runtime facts are authority",
        "data, not instructions",
        "read_only_question",
        "next_round_requirement",
        "unsupported_write",
        "requirement_text",
        "target_section_hint",
        "canonical extraction input",
        "normalized text as provenance",
        "Never claim that you executed a service action",
        "deterministic runtime finalization",
    ]:
        assert phrase in prompt


def test_conversation_agent_prompt_omits_unsafe_tool_claims() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    for phrase in [
        "operate only through the BFF/runtime-control tools made available to you",
        "Use the runtime-control next-round requirement tool",
        "You can call tools",
        "Use tools directly",
        "Tools are available to you",
    ]:
        assert phrase not in prompt
