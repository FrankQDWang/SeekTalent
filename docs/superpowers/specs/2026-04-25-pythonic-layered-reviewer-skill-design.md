# Pythonic Layered Reviewer Skill Design

## Goal

Create a global Codex skill named `pythonic-layered-reviewer`.

This skill should provide strict, repo-first review of Python 3.12+ codebases through a layered Pythonic lens. It is not tied to the current repository. The current repository only stores the design document for the skill.

The skill should help Codex review:

- idiomatic Python usage
- local design quality
- scale-aware engineering boundaries

It should be usable across repositories without depending on project-local files.

## Positioning

This is a review skill, not a code-writing assistant and not a tutorial skill.

Default posture:

- strict on high-confidence problems
- repo-first rather than snippet-first
- grounded in mainstream Python consensus
- biased toward pragmatic simplicity
- conscious of product-scale maintenance and collaboration

The governing value frame is:

`mainstream consensus as the skeleton, scale-aware pragmatic minimalism as the soul`

This means:

- use PEPs, official docs, and mature Python practice as the authority base
- prefer common Python-native idioms over hand-written ceremony
- allow necessary abstraction when it materially improves maintenance, API stability, or team collaboration
- reject premature abstraction, speculative flexibility, and no-payoff indirection

## Installation Target

This skill should be implemented as a global, self-contained skill under:

- `~/.codex/skills/pythonic-layered-reviewer/`

It should not require the current repository to exist. It may mention this repository's design doc during creation, but the shipped skill must be portable.

## In Scope

The skill should review the whole Python engineering surface, not just `.py` syntax.

In scope:

- Python source code
- tests
- public API shape
- package and module boundaries
- type usage where it affects clarity
- exception and resource-handling style
- project-level structure signals that affect Pythonic maintainability

The skill should work best when the user asks for review of:

- a full repository
- a package or subdirectory
- a group of related modules

It may still review single files, but that is not the primary target.

## Out of Scope

The skill should default to caution in these areas:

- performance strategy unless code is obviously wasteful or misleading
- concurrency model choice unless the code clearly violates Pythonic clarity or safety
- framework selection unless usage is clearly unidiomatic or over-engineered
- infrastructure and platform selection
- business-domain decomposition unless it directly harms readability or module boundaries

It should not act like a generic architecture critic, product manager, or framework partisan.

## Review Layers

The skill should always review in this order.

### 1. Idioms

Check whether the code uses mature, common, Python-native ways to express intent.

Priority signals:

- `zip`, `enumerate`, `extend`, `defaultdict`, `Counter`
- comprehensions and generator expressions
- `any`, `all`, `sum`
- `pathlib`
- context managers
- standard-library-first problem solving
- natural exception chaining
- natural container and iteration semantics
- Python 3.12+ features when they make code meaningfully clearer

The skill should actively prefer mainstream Python idioms over hand-written boilerplate when the idiom is stable, common, and readable.

### 2. Design

Check whether local structure matches Python's strengths.

Priority signals:

- short, direct functions
- explicit state
- classes only when they hold real state or lifecycle
- clear names with domain meaning
- minimal but sufficient abstraction
- natural API shapes
- tests that clarify behavior instead of mirroring internals
- avoidance of Java/C#-style ceremony in Python

### 3. Scale

Check whether repository structure supports product growth without unnecessary complexity.

Priority signals:

- package and module cohesion
- dependency direction
- public API stability and boundary clarity
- leakage of internals across modules
- circular dependencies
- abstraction bloat
- configuration bloat
- speculative extension points
- structures that actively increase collaboration cost as the repo grows

## Review Flow

The skill should follow a fixed review flow instead of free-form commentary.

1. Determine scope: file, directory, or repository.
2. For repository or directory review, inspect high-level structure before local details.
3. Identify public packages, entrypoints, tests, and major configuration surfaces.
4. Form a repo-level diagnosis before drilling into idioms.
5. Report high-confidence Pythonic and scale problems first.
6. Report lower-confidence stylistic preferences last, or suppress them.

The skill should avoid "comment on everything" behavior. It should prune aggressively and keep findings high-signal.

## Output Shape

Default output should be two-layered:

1. findings
2. high-level diagnosis

The findings section should be split into:

- `Hard findings`
- `Strong suggestions`
- `Preference notes`

The diagnosis section should summarize the codebase using labels such as:

- `Pythonic and scalable`
- `Pythonic but locally overgrown`
- `Readable but unidiomatic`
- `Over-abstracted for current needs`
- `Fragile at scale`

The diagnosis section should not introduce new findings. It should only compress the overall state into a useful high-level read.

## Finding Taxonomy

### Hard Findings

Use only when the issue has clear practical cost.

Examples:

- obvious anti-Pythonic boilerplate that harms readability
- obvious misuse or avoidance of mature Python-native idioms
- design ceremony with no demonstrated payoff
- repository structures that create real maintenance or boundary risk
- public APIs that unnecessarily leak internal structure

### Strong Suggestions

Use for high-value improvements that are not mandatory fixes.

Examples:

- replacing repeated control-flow boilerplate with common Python idioms
- simplifying abstractions that add local complexity
- improving exception or resource semantics
- tightening module boundaries before they become active pain

### Preference Notes

Use only when both approaches are viable but one is more idiomatic.

Examples:

- a more natural standard-library choice
- a more concise but not materially safer rewrite
- stylistic alignment with mainstream Python practice

Preference notes must not be written as defects.

## Review Heuristics

The skill should encode the following durable heuristics.

### Idiom Heuristics

- Prefer common Python-native constructs over custom control-flow scaffolding.
- Prefer readable standard-library tools over bespoke utility wrappers.
- Prefer generator expressions for aggregation paths when no intermediate collection is needed.
- Prefer context managers for resources and temporary state.
- Prefer Python containers and iterator tools over manual bookkeeping when the intent becomes clearer.
- Prefer common, recognizable Python idioms, not cleverness.

### Design Heuristics

- Prefer module-level functions unless a class is justified by durable state or lifecycle.
- Prefer direct data flow over hidden state containers.
- Prefer names that explain domain meaning, not architectural vanity.
- Prefer small, clear APIs over highly flexible ones.
- Prefer one clear responsibility per unit.
- Prefer removing dead layers over wrapping them in more layers.

### Scale Heuristics

- Prefer cohesive packages with obvious boundaries.
- Prefer one-way dependency direction where possible.
- Prefer public APIs that expose stable intent rather than internal representation.
- Prefer targeted abstraction over generic extension frameworks.
- Prefer structures that reduce coordination cost across contributors.

## Bias Rules

The skill should be intentionally biased in these ways:

- prefer mature Python idioms over hand-written boilerplate
- prefer readable native constructs over framework-shaped abstractions
- prefer necessary abstraction over absolute minimalism
- prefer mainstream Python consensus over novelty
- prefer scale-aware clarity over local cleverness

It should explicitly reject these biases:

- "shorter is always better"
- "newer syntax is always better"
- "more abstraction is more scalable"
- "framework convention automatically equals Pythonic"

## Boundary Rules

The skill should suppress or downgrade comments when:

- the judgment depends primarily on product strategy
- the judgment depends on deep business semantics
- the tradeoff is mainly framework-local rather than Pythonic
- the issue is stylistic but produces no clear readability or maintenance gain
- the code is stable and the rewrite value is marginal

Strictness should mean refusing to ignore high-confidence problems, not commenting on every possible improvement.

## Skill Layout

The first version should be documentation-first, with no mandatory scripts.

Recommended layout:

- `~/.codex/skills/pythonic-layered-reviewer/SKILL.md`
- `~/.codex/skills/pythonic-layered-reviewer/references/idioms.md`
- `~/.codex/skills/pythonic-layered-reviewer/references/design.md`
- `~/.codex/skills/pythonic-layered-reviewer/references/scale.md`
- `~/.codex/skills/pythonic-layered-reviewer/references/sources.md`

### `SKILL.md`

Should contain:

- trigger conditions
- scope and positioning
- layered review flow
- output format
- finding taxonomy
- boundary rules

### `references/idioms.md`

Should contain:

- Python-native idiom preferences
- common anti-pattern to idiom mappings
- standard-library-first guidance

### `references/design.md`

Should contain:

- abstraction rules
- function, class, and module review rules
- naming and API-shape guidance
- Python-vs-Java/C# contrast where useful

### `references/scale.md`

Should contain:

- package and boundary heuristics
- public API stability checks
- dependency direction rules
- circular dependency and bloat smells

### `references/sources.md`

Should record the authority stack and how to use it:

- PEP 20
- PEP 8
- Python 3.12 official docs
- selected standard-library docs
- a small set of high-quality community references

The shipped skill should not depend on any project-local or machine-local research document to function.

## Reference Sources

The skill should treat sources with explicit precedence.

Primary authority:

- PEP 20
- PEP 8
- Python 3.12 official documentation

Secondary authority:

- official standard-library documentation for `typing`, `pathlib`, `collections`, `contextlib`, `itertools`, `dataclasses`, and `enum`

Tertiary support:

- Trey Hunner / Python Morsels style material
- Real Python articles that align with official guidance
- Sourcery rule ideas only as inspiration, not as the skill's authority base
- the user's research document only during skill drafting, not as a runtime dependency

## Success Criteria

The first version is successful if it causes Codex to:

- review repositories with repo-level structure awareness before line-level nitpicks
- aggressively recommend mainstream Python-native idioms
- distinguish real findings from mere preferences
- preserve a pragmatic minimalism bias without rejecting necessary abstraction
- recognize scale signals such as boundary drift, circular dependencies, and public API instability
- stay cautious outside its authority band

This first version should optimize for judgment quality and consistency, not automation breadth.
