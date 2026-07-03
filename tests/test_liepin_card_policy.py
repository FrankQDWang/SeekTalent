from __future__ import annotations

from seektalent.providers.liepin.card_policy import (
    LiepinCardDecisionAction,
    LiepinCardSummary,
    build_liepin_card_decisions,
)


def _summary(
    candidate_id: str,
    provider_rank: int,
    *,
    title: str | None = None,
    company: str | None = None,
    city: str | None = None,
    skills: tuple[str, ...] = (),
    experience: tuple[dict[str, object], ...] = (),
) -> LiepinCardSummary:
    return LiepinCardSummary(
        candidate_resume_id=candidate_id,
        provider_rank=provider_rank,
        current_or_recent_company=company,
        current_or_recent_title=title,
        city=city,
        skill_tags=skills,
        experience_preview=experience,
    )


def test_provider_rank_is_primary_after_hard_filters_and_budget() -> None:
    decisions = build_liepin_card_decisions(
        cards=[
            _summary(
                "rank-1",
                1,
                title="Backend Engineer",
                company="Ranking Platform",
                skills=("FastAPI", "ranking"),
            ),
            _summary("rank-2", 2, title="Store Manager"),
            _summary(
                "rank-3",
                3,
                title="Python Engineer",
                company="Search Services",
                skills=("Python", "FastAPI"),
            ),
            _summary(
                "rank-4",
                4,
                title="Backend Engineer",
                experience=({"company": "Distributed Systems", "title": "FastAPI Engineer"},),
            ),
        ],
        query_terms=("FastAPI", "ranking"),
        job_title="Backend Engineer",
        max_detail_recommendations=2,
    )

    recommended = [item for item in decisions if item.action == LiepinCardDecisionAction.RECOMMEND_DETAIL]

    assert [item.candidate_resume_id for item in recommended] == ["rank-1", "rank-3"]
    assert [item.provider_rank for item in recommended] == [1, 3]
    assert [item.card_policy_rank for item in recommended] == [1, 2]
    assert decisions[1].action == LiepinCardDecisionAction.REJECT_OBVIOUS_MISMATCH
    assert "obvious_role_mismatch" in decisions[1].reason_codes


def test_missing_card_fields_hold_instead_of_recommending_detail() -> None:
    decisions = build_liepin_card_decisions(
        cards=[
            _summary("thin-card", 1, title="Engineer"),
        ],
        query_terms=("FastAPI", "ranking"),
        job_title="Backend Engineer",
        max_detail_recommendations=1,
    )

    assert decisions[0].action == LiepinCardDecisionAction.HOLD_INSUFFICIENT_CARD_SIGNAL
    assert decisions[0].budget_reason_code == "insufficient_card_signal"


def test_chinese_card_terms_can_recommend_detail() -> None:
    decisions = build_liepin_card_decisions(
        cards=[
            _summary(
                "cn-card",
                1,
                title="高级数据开发工程师",
                company="业务线科技公司",
                skills=("数据开发", "数据仓库", "数据治理", "Python", "Java"),
            ),
        ],
        query_terms=("数据开发", "数据仓库", "数据治理"),
        job_title="数据开发专家",
        max_detail_recommendations=1,
    )

    assert decisions[0].action == LiepinCardDecisionAction.RECOMMEND_DETAIL
    assert "matched_card_terms" in decisions[0].reason_codes


def test_obvious_mismatch_does_not_consume_recommendation_budget() -> None:
    decisions = build_liepin_card_decisions(
        cards=[
            _summary("wrong", 1, title="Store Manager"),
            _summary(
                "right",
                2,
                title="Backend Engineer",
                company="Ranking Platform",
                skills=("FastAPI", "ranking"),
            ),
        ],
        query_terms=("FastAPI", "ranking"),
        job_title="Backend Engineer",
        max_detail_recommendations=1,
    )

    recommended = [item for item in decisions if item.action == LiepinCardDecisionAction.RECOMMEND_DETAIL]

    assert [item.candidate_resume_id for item in recommended] == ["right"]


def test_card_policy_has_no_normalized_card_text_field() -> None:
    fields = LiepinCardSummary.__dataclass_fields__

    assert "normalized_card_text" not in fields
