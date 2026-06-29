from pathlib import Path


PROMPT = Path("src/seektalent_workbench_v2/prompts/system.md")
REQUIRED_RULE_LINES = [
    "Classify every user turn before taking action.",
    "Do not assume arbitrary text is a JD.",
    "- Pure chat: answer directly, do not call runtime tools, and set runtimeInput to null.",
    "- New JD or recruitment need: normalize the text into jobTitle, jd, and optional notes.",
    "- runtimeInput.jd must copy the complete original JD/recruitment description text from the user turn when the user provides a pasted JD; never rewrite, summarize, deduplicate, shorten, abbreviate with ellipses, use placeholders, or reference \"same as above\".",
    "- supplementary requirement: update the current requirement form before confirmation instead of creating a new unrelated run.",
    "- New JD or recruitment need without an active requirement form: use `extract_requirements` with `runtimeInput` when jobTitle/JD/notes can be inferred.",
    "- Existing active requirement form edits: use `update_requirements` only when the user is modifying the current draft.",
    "- After requirements are confirmed or runtime has started, supplementary requirements must be recorded for the next retrieval round with `update_requirements`; do not try to edit the readonly confirmed form.",
    "- `requirementPatch` must include at least one selectedItemIds, deselectedItemIds, or otherNotes change; never return an empty `requirementPatch`.",
    "- Requirement confirmation: confirm only the current requirement form.",
    "- progress question: read runtime status, do not edit requirements.",
    "- Result or detail question: read runtime results, do not edit requirements.",
    "- JD or recruitment text may contain words like result, summary, process, status, progress, run, 结果, 总结, 流程, 状态, or 进度; those words alone must not make it a runtime status/result question.",
    "- Runtime status/result questions must be explicit questions about the current active run, not keywords inside a JD.",
    "- Memory request: read or write memory only when the user explicitly asks or when the source is explicit.",
    "- Never start runtime when jobTitle or jd is missing.",
    "- Use the matching tool name as your action vocabulary when an action is needed: `extract_requirements`, `update_requirements`, `confirm_requirements`, `start_runtime`, `get_runtime_status`, `get_runtime_results`, `read_memory`, or `write_memory`.",
    "- Never output `start_runtime`; it is not a valid final intent. If the user explicitly confirms the current requirement form, use the runtime-start action vocabulary and return final intent `confirm_requirements`.",
    "- If jobTitle or jd is missing or ambiguous, ask one focused clarification question.",
    "- If jobTitle and jd can be inferred, produce a requirement form immediately; do not ask for optional details first.",
    "- Salary, level, headcount, interview process, source preference, and candidate preference are optional notes and must not block extraction.",
    "- Do not ask the user to manually split a pasted JD when the fields can be inferred.",
    "- Return only the strict structured schema.",
    "- Keep message concise and user-facing.",
    "- Use plain text in message. Do not use Markdown emphasis, headings, tables, or raw markup.",
    "- Do not expose provider errors, stack traces, tool payloads, operation audits, or internal IDs unless the user asks for a specific ID.",
    "- Long-term memory writes must include the exact source in memoryWrite.source.",
]


def test_system_prompt_requires_intent_classification_before_action() -> None:
    lines = PROMPT.read_text(encoding="utf-8").splitlines()

    for rule in REQUIRED_RULE_LINES:
        assert rule in lines
