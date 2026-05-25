# Liepin Filter Parity Design

## Goal

Bring Liepin card search to the same Runtime-owned filter semantics as CTS for the filters the real Liepin recruiter UI can apply safely: location, work experience, age, degree, recruitment type, and school type.

## Background

The Runtime now creates one shared logical query bundle for CTS and Liepin. CTS already projects Runtime filter intent into provider-native filters with stable range-bucket rules. Liepin now receives Runtime intent and can apply city, work experience, and age through the browser, but real Chrome inspection showed three gaps:

- The Liepin page has both `目前城市` and `期望城市`; the current click-by-label behavior can hit the wrong `北京`.
- The full historical `数据开发专家` JD includes `本科`, `统招`, and `985/211`, but Liepin currently does not project these into the page filters.
- Work-experience values that do not exactly match Liepin buckets need the same deterministic projection discipline CTS already uses.

Real Chrome inspection of `https://h.liepin.com/search/getConditionItem#session` showed these relevant controls:

- `目前城市`: 不限, 北京, 上海, 广州, 苏州, 武汉, 成都, 杭州, 福州, 其他
- `期望城市`: 不限, 上海, 北京, 佛山, 西安, 深圳, 武汉, 合肥, 杭州, 其他
- `工作年限`: 不限, 应届生, 1-3年, 3-5年, 5-10年, 10年以上, 自定义
- `教育经历`: 不限, 本科, 硕士, 博士/博士后, 大专, 中专/中技, 高中及以下
- `统招要求`: 统招/非统招 dropdown
- `院校要求`: 不限, 211, 985, 双一流, 海外留学
- `年龄`: min/max text inputs

## Product Requirements

1. Runtime remains the source of truth.
   - Liepin must not parse JD text itself.
   - Liepin must not invent city, experience, age, degree, recruitment type, or school-type filters.
   - Liepin may only project `RuntimeSourceQueryIntent.filter_intents` and `RuntimeSourceQueryIntent.location_intent`.

2. Liepin city must target the correct UI section.
   - JD job-location filters map to `期望城市` by default.
   - The adapter must not click the first matching city label on the page when the same city appears under both `目前城市` and `期望城市`.
   - Multi-city requirements keep Runtime's existing location plan: one Liepin browser search per city target, using Runtime balanced or priority order and per-target requested count.

3. Liepin work-experience projection mirrors CTS.
   - Supported Liepin buckets are `应届生`, `1-3年`, `3-5年`, `5-10年`, and `10年以上`.
   - For numeric ranges, use the same overlap-and-tie-order behavior as CTS.
   - If a value spans three or more buckets, has no overlap, or cannot be normalized, do not apply a native experience filter; keep it as Runtime-only and record a safe partial reason.
   - Do not use Liepin `自定义` in this pass; it is more fragile and needs separate UI validation.

4. Liepin age projection remains label/input safe.
   - Existing label projection may continue for values matching visible labels.
   - If the page requires min/max input for an age value, this pass may leave it Runtime-only rather than typing into uncontrolled inputs.
   - Any skipped age filter must be source-scoped and public-safe.

5. Liepin education filters must support the full historical JD.
   - `degree_requirement=本科` maps to the `教育经历` `本科` option.
   - `school_type_requirement` values containing `统招` map to the `统招要求` dropdown.
   - `school_type_requirement` values containing `211`, `985`, or `双一流` map to `院校要求`.
   - If the real page cannot select multiple school-type values at once, apply the broadest safe value and keep the rest Runtime-only with a protected trace note.

6. Public payloads stay business-safe.
   - Public reason codes may say `source_filter_partial`, `source_filter_unavailable`, or `source_filter_applied`.
   - Public responses must not expose browser command details, OpenCLI/Pi terms, selectors, raw page state, cookies, authorization, local paths, or protected artifacts.

7. Real UI verification is mandatory.
   - Completion requires a real Chrome run using the historical `session_814bd4d124df48ce` input for `数据开发专家`.
   - The run must capture screenshots before search, after Liepin filters are applied, during Workbench runtime, and after results are visible.
   - Protected action trace must prove `期望城市=北京`, `教育经历=本科`, `统招要求=统招`, and `院校要求` were attempted.
   - Cleanup must close agent-created Chrome tabs and run OpenCLI orphan cleanup.

## Non-Goals

- Do not add salary, industry, current position, expected position, language, active status, gender, job-search status, or resume-language filters in this pass.
- Do not add Liepin detail-opening behavior.
- Do not change Runtime round decision, finalization, or CTS behavior except shared helper reuse and regression tests.
- Do not implement Liepin custom range input for work experience until the button/input behavior is validated separately.

## Acceptance Criteria

1. Liepin native filter payload distinguishes city section, with `期望城市` used for Runtime job-location filters.
2. Multi-city Runtime location intent emits one Liepin compiled query per city target, with requested counts matching Runtime allocation.
3. Liepin work-experience projection uses CTS-style bucket overlap/tie rules and skips unsafe spans instead of guessing.
4. Liepin degree, recruitment type, and school type filters are represented in typed safe payloads.
5. OpenCLI browser actions click options within their intended filter sections, not by global label match.
6. Unsupported or partially applied filters are recorded as source-scoped safe partials, not Runtime invariant failures.
7. Existing CTS projection tests still pass.
8. New unit tests cover city section disambiguation, multi-city allocation, experience mismatch projection, education/school-type projection, and public-safe partial reasons.
9. Focused Python tests pass.
10. `uv run ruff check` passes for changed files.
11. `uv run ty check` passes for changed Python files.
12. `uv build` passes.
13. `cd apps/web-svelte && bun run build` passes.
14. Real Chrome QA with the complete historical `数据开发专家` input proves the Liepin page applies the intended filters or records safe partials, and Workbench dual-source search still reaches visible results.
