# Scoring Scale and Liepin Source URL Patch Design

## Goal

Ship a small corrective release that restores a coherent 0–100 scoring scale, makes recommendation language and Workbench visibility use the same eligibility rule, and restores stable Liepin resume-detail links without weakening the run-wide detail-open claim boundary.

## Confirmed Failures

The production run `rtrun_1f6c7368366843c48c8829ca3ed096b1` produced scores `90, 50, 1, 1, 1, 1, 1` and fourteen zeroes. Several candidates with `fit_bucket="fit"` scored `1`, while a candidate with strong technical evidence scored `90` but was correctly classified `not_fit` because the two-year hard experience requirement was not met. The deterministic `60/25/15` weighted-average implementation is arithmetically correct; the defect is that the LLM scoring contract lacks explicit scale anchors and does not reject `fit` outputs whose calculated overall score is below 60.

The same run persisted no `sourceUrl`. In claim-aware Liepin detail capture, the current URL is validated against `provider_candidate_key_hash`, but the verified URL is not copied into the detail payload. The downstream normalizer and Workbench projection already support a safe `sourceUrl`, so the data is lost at capture time.

## Scoring Contract

Keep the existing weights:

- must-have: 60
- preferred: 25 when applicable
- inverted risk: 15 when applicable
- absent optional dimensions are removed and the remaining weights are normalized

Define the 0–100 anchors in the scoring prompt:

- 90–100: highly matched
- 80–89: strong match
- 70–79: basic match
- 60–69: weak match with material gaps
- below 60: not recommended

The scoring output validator calculates the deterministic overall score from the model's dimension scores. `fit_bucket="fit"` with overall below 60 is invalid structured output and triggers the existing bounded `ModelRetry`. A high capability score may coexist with `fit_bucket="not_fit"` when a hard requirement fails; the score is retained for diagnostics, but the candidate is not recommendation-eligible.

Define one shared recommendation predicate:

```python
fit_bucket == "fit" and overall_score >= 60
```

Use it for Workbench candidate projection and round resume-quality comments. The commenter receives only recommendation-eligible candidates. If none exist, runtime emits the deterministic text `本轮暂无达到 60 分推荐标准且满足硬性条件的候选人。` and does not ask the LLM to invent a qualitative count.

## Liepin Source URL Contract

After claim-aware detail capture verifies that the current detail URL hashes to the expected provider candidate key, derive and persist a canonical source URL containing only:

```text
https://h.liepin.com/resume/showresumedetail/?res_id_encode=<validated subject>
```

The subject must satisfy the same strict parser already used by `stable_liepin_detail_candidate_key_hash`: exact HTTPS host and path, exactly one unescaped alphanumeric `res_id_encode`, and no encoded aliases. Volatile navigation parameters such as `index`, `position`, `cur_page`, `pageSize`, and fragments are discarded. Invalid or mismatched URLs still fail capture; no URL is fabricated from a hash.

## Scope

This patch changes the scoring prompt, structured-output validation, shared recommendation eligibility, quality-comment input, Workbench projections, claim-aware Liepin detail capture, tests, and the patch version. It does not change the scoring weights, lower the 60-point threshold, replay completed runs, or replace the source link with a new browser-action API.

## Verification

- A `fit` draft whose weighted score is below 60 retries and cannot materialize as a recommended candidate.
- A corrected retry with anchored dimension scores succeeds.
- A 90-point `not_fit` candidate remains diagnostic data but is absent from Workbench recommendations and quality-comment inputs.
- A round with no eligible candidates emits the deterministic no-match sentence.
- Claim-aware capture persists a canonical Liepin detail URL after identity verification and strips volatile parameters.
- Malformed, encoded-alias, duplicate-subject, wrong-host, and identity-mismatch URLs remain rejected.
- Focused tests, full Python tests, Workbench contract, Ruff, Ty, package build, release CI, PyPI publication, and local Domi installation pass.
