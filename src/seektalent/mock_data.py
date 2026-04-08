from __future__ import annotations

from seektalent.locations import normalize_location
from seektalent.models import RetrievedCandidate_t, stable_fallback_resume_id


def _candidate(
    candidate_id: str | None,
    *,
    candidate_name: str,
    title: str,
    current_company: str,
    location: str,
    work_year: int | None,
    industry: str,
    projects: list[str],
    education: list[str],
    experiences: list[dict[str, str]],
    work_summaries: list[str],
) -> RetrievedCandidate_t:
    canonical_location = normalize_location(location)
    work_experience_summaries = [
        " | ".join(part for part in [item.get("company", ""), item.get("title", ""), item.get("summary", "")] if part)
        for item in experiences
    ]
    raw_payload = {
        "candidateName": candidate_name,
        "currentCompany": current_company,
        "title": title,
        "industry": industry,
        "workExperienceList": experiences,
        "educationList": education,
    }
    stable_id = candidate_id or stable_fallback_resume_id(
        {
            "candidate_name": candidate_name,
            "title": title,
            "current_company": current_company,
            "location": canonical_location,
            "experiences": experiences[:2],
        }
    )
    raw_payload["resume_id"] = stable_id
    return RetrievedCandidate_t(
        candidate_id=stable_id,
        age=28 + min(work_year or 5, 10),
        gender="未知",
        now_location=canonical_location,
        expected_location=canonical_location,
        years_of_experience_raw=work_year,
        education_summaries=list(education),
        work_experience_summaries=work_experience_summaries,
        project_names=list(projects),
        work_summaries=list(work_summaries),
        search_text=" ".join(
            [
                candidate_name,
                title,
                current_company,
                canonical_location,
                industry,
                *projects,
                *education,
                *work_experience_summaries,
                *work_summaries,
            ]
        ),
        raw_payload=raw_payload,
    )


def load_mock_resume_corpus() -> list[RetrievedCandidate_t]:
    return [
        _candidate(
            "mock-r001",
            candidate_name="Lin Qian",
            title="Senior Python Agent Engineer",
            current_company="Hewa Talent Cloud",
            location="上海",
            work_year=8,
            industry="招聘科技",
            projects=["Resume ranking CLI", "Pydantic AI search workflow"],
            education=["复旦大学 计算机 硕士"],
            experiences=[
                {
                    "company": "Hewa Talent Cloud",
                    "title": "Senior Python Agent Engineer",
                    "summary": "Built Python agent workflow and retrieval ranking stack.",
                }
            ],
            work_summaries=["python", "agent", "retrieval", "ranking", "trace"],
        ),
        _candidate(
            "mock-r002",
            candidate_name="Zhou Ming",
            title="LLM Search Engineer",
            current_company="SearchLab",
            location="上海",
            work_year=7,
            industry="企业 SaaS",
            projects=["Candidate search pipeline", "Evaluation reranking service"],
            education=["上海交通大学 软件工程 硕士"],
            experiences=[
                {
                    "company": "SearchLab",
                    "title": "LLM Search Engineer",
                    "summary": "Owned Python retrieval pipeline and deterministic reranking.",
                }
            ],
            work_summaries=["python", "retrieval", "ranking", "observability"],
        ),
        _candidate(
            "mock-r003",
            candidate_name="Wu Chen",
            title="Python Agent Platform Engineer",
            current_company="TalentMind",
            location="杭州",
            work_year=6,
            industry="人力资源科技",
            projects=["Prompt registry", "Trace store"],
            education=["浙江大学 计算机 本科"],
            experiences=[
                {
                    "company": "TalentMind",
                    "title": "Python Agent Platform Engineer",
                    "summary": "Built recruiter tooling with Python agent orchestration.",
                }
            ],
            work_summaries=["python", "agent", "trace", "workflow"],
        ),
        _candidate(
            "mock-r004",
            candidate_name="He Fang",
            title="Search Backend Engineer",
            current_company="JobLink",
            location="北京",
            work_year=9,
            industry="招聘平台",
            projects=["CTS adapter", "OpenAPI contract validation"],
            education=["北京邮电大学 计算机 本科"],
            experiences=[
                {
                    "company": "JobLink",
                    "title": "Search Backend Engineer",
                    "summary": "Integrated candidate search API adapters from validated specs.",
                }
            ],
            work_summaries=["python", "cts", "openapi", "filters"],
        ),
    ]
