from __future__ import annotations

from hashlib import sha1

from seektalent.company_discovery.models import TargetCompanyCandidate, TargetCompanyPlan
from seektalent.models import QueryTermCandidate


def inject_target_company_terms(
    pool: list[QueryTermCandidate],
    plan: TargetCompanyPlan,
    first_added_round: int = 0,
    accepted_limit: int | None = None,
) -> list[QueryTermCandidate]:
    output = list(pool)
    seen_families = {item.family for item in pool}
    seen_company_keys = {_company_key(item.term) for item in pool}
    blocked = {_company_key(name) for name in plan.excluded_companies}
    blocked.update(_company_key(name) for name in plan.holdout_companies)
    blocked.update(_company_key(name) for name in plan.rejected_companies)

    accepted = 0
    for candidate in plan.accepted_targets:
        if accepted_limit is not None and accepted >= accepted_limit:
            break
        if candidate.intent == "exclude":
            continue
        company_keys = _candidate_keys(candidate)
        if company_keys & blocked or company_keys & seen_company_keys:
            continue
        family = f"company.{_company_slug(candidate.name)}"
        if family in seen_families:
            continue
        output.append(
            QueryTermCandidate.model_construct(
                term=candidate.name,
                source=candidate.source if candidate.source.startswith("explicit_") else "company_discovery",
                category="company",
                priority=max(20, 20 + accepted),
                evidence=_company_evidence(candidate),
                first_added_round=first_added_round,
                active=True,
                retrieval_role="target_company",
                queryability="admitted",
                family=family,
            )
        )
        seen_families.add(family)
        seen_company_keys.update(company_keys)
        accepted += 1
    return output


def _company_evidence(candidate: TargetCompanyCandidate) -> str:
    if candidate.rationale:
        return candidate.rationale
    snippets = [item.snippet for item in candidate.evidence if item.snippet]
    return " | ".join(snippets)


def _company_key(name: str) -> str:
    return _company_slug(name)


def _candidate_keys(candidate: TargetCompanyCandidate) -> set[str]:
    return {_company_key(name) for name in [candidate.name, *candidate.aliases] if name}


def _company_slug(name: str) -> str:
    slug = "".join(char.lower() for char in name.strip() if char.isalnum())
    if slug:
        return slug
    return sha1(name.strip().casefold().encode("utf-8")).hexdigest()[:12]
