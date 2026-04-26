# AGENTS.md

## Purpose

This repository is in active productization.
Optimize for readable Python, safe change velocity, and maintainable repository-scale structure.

Default to pragmatic simplicity, but do not preserve experimental-stage shortcuts once they start increasing maintenance cost, boundary confusion, or regression risk.

## Core Philosophy

- Write idiomatic Python, not Java/C# in Python syntax.
- Prefer direct code over ceremony.
- Prefer the simplest design that remains clear at repository scale.
- Do not engineer for hypothetical future needs.
- Allow necessary abstraction when it improves boundaries, API stability, or collaboration cost.
- Reject speculative flexibility, architecture vanity, and no-payoff indirection.
- Fail fast inside trusted internal logic.
- Be more deliberate at external boundaries, user-facing flows, and integration points.

## Python First

- Write idiomatic Python, not Java/C# in Python syntax.
- Prefer module-level functions over classes by default.
- Only introduce a class when it holds real state, lifecycle, or identity.
- Never create `Utils`, `Helpers`, `Managers`, or similar junk containers for stateless code.
- Prefer simple protocols and direct interfaces over hierarchy-heavy design.

## Pydantic Usage

- Use Pydantic models where typed boundaries or structured input/output genuinely need them.
- Keep models small, explicit, and close to usage.
- Do not create model layers for architecture vanity.
- Do not wrap every internal value in a model.
- Add validators only when they enforce real business meaning or prevent a real bug.
- Do not add validation theater.

## Naming
- Use simple, literal names.
- Favor names that describe the domain meaning, not implementation trivia.
- Do not use clever abbreviations unless they are already standard in the codebase.
- Do not invent grand architectural names for small local code.

## Function And Module Design

- Prefer short functions with obvious inputs and outputs.
- Keep the main path readable top-to-bottom.
- Keep logic inline when extraction would only add indirection.
- Extract functions when it improves clarity, reuse, or testability.
- Keep module responsibilities legible.
- If a file becomes hard to reason about, split it by responsibility instead of stacking more local helpers into it.
- Do not add layers just to look organized.

## State And Mutation

- Keep state explicit.
- Prefer passing values directly over hiding state in broad containers.
- Mutate data only when mutation is the clearest choice.
- Do not simulate immutability with pointless copying.
- Do not add getters and setters without a real boundary reason.

## Abstraction And Boundaries

- Treat local simplicity as the default, not an absolute.
- Introduce abstraction only when it clearly improves ownership, boundaries, reuse across important paths, or API stability.
- Keep public APIs small, explicit, and stable.
- Do not leak internal storage or implementation details across modules without a strong reason.
- Avoid junk-drawer modules that mix unrelated responsibilities.
- Avoid circular dependencies. If modules want to know too much about each other, the boundary is probably wrong.
- Do not create extension points, generic bases, or extra indirection without real pressure behind them.

## Error Handling
- Fail loudly on invalid assumptions.
- Do not swallow exceptions.
- Do not add retry/fallback/recovery logic unless explicitly requested.
- Do not convert every failure into a custom error type.
- Add error handling where it changes developer understanding or user outcome in a meaningful way.

## Error Handling And Validation

- Fail fast inside trusted internal logic.
- Be more deliberate at external boundaries, user-facing flows, and integration points.
- Validate input where it changes user outcome, operator understanding, or downstream safety.
- Do not swallow exceptions.
- Preserve clear error semantics.
- Do not add fallback chains, retry logic, or recovery scaffolding unless the use case actually requires it.
- Reject both defensive-programming spam and careless boundary handling.

## LLM Structured Output Exception
- A bounded retry is allowed only when an LLM response fails structured output or schema validation.
- This exception is only for "the model answered, but the structured parse failed", followed by an immediate re-request.
- Keep it small and explicit. Prefer a single retry unless a task explicitly requires more.
- Do not extend this exception to network failures, tool failures, timeouts, rate limits, or generic recovery logic.
- Do not add fallback model chains unless explicitly requested.

## Comments and Docstrings
- Write comments only when the reason is non-obvious.
- Do not comment what the code already says.
- Prefer self-explanatory code over explanatory comments.
- Use docstrings for public functions or non-obvious behavior.
- Keep docstrings short and factual. Python’s own style guidance treats these as conventions for clarity, not ceremony.  [oai_citation:1‡Python Enhancement Proposals (PEPs)](https://peps.python.org/pep-0257/)

## Code Volume Discipline
- Minimize line count, but never at the cost of clarity.
- Shorter is better only when still readable.
- Do not introduce indirection to look “clean”.
- Remove dead layers, dead wrappers, and dead abstractions aggressively.

## When Changing Code
- Make the smallest change that fully solves the problem.
- Preserve working code unless there is a real reason to change it.
- Do not rewrite large sections without explicit need.
- Do not reshape the whole file for style reasons alone.
- If editing existing logic, prefer surgical diffs over broad rewrites.

## What to Avoid
- No enterprise architecture cosplay.
- No premature extensibility.
- No framework-shaped code when plain Python is enough.
- No speculative generic abstractions.
- No “base” classes created just in case.
- No defensive programming spam.
- No configuration bloat.
- No ceremony added just to look professional.

## Decision Rule
When several implementations are possible, prefer the one that is:
1. correct
2. smaller
3. clearer
4. easier to change locally

If two options are equally correct, choose the less abstract one.

## Output Discipline
- For non-trivial feature work, start with a short numbered checklist before writing code.
- Do not produce large rewrites unless explicitly requested.
- When modifying existing code, prefer minimal before/after diffs.
- Keep explanations short. The code should carry the weight.

## Priority
In this repository, local simplicity beats theoretical architecture.
Readable Python beats pattern-heavy design.
Working code beats elaborate scaffolding.
