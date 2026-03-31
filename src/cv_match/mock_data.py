from __future__ import annotations

from cv_match.models import ResumeCandidate, stable_fallback_resume_id


def _candidate(
    stable_id: str | None,
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
    skills: list[str] | None = None,
    languages: list[str] | None = None,
    failure_mode: str = "none",
    headline: str | None = None,
    full_text: str | None = None,
) -> ResumeCandidate:
    work_experience_summaries = [
        " | ".join(part for part in [item.get("company", ""), item.get("title", ""), item.get("summary", "")] if part)
        for item in experiences
    ]
    raw_payload = {
        "candidate_name": candidate_name,
        "headline": headline or title,
        "current_company": current_company,
        "skills": skills if skills is not None else list(work_summaries),
        "languageTags": languages or [],
        "workExperienceList": experiences,
        "educationList": education,
        "fullText": full_text
        or " ".join(
            [
                title,
                current_company,
                *projects,
                *(item.get("summary", "") for item in experiences),
                *work_summaries,
            ]
        ),
        "mock_score_failure_mode": failure_mode,
    }
    if stable_id:
        raw_payload["resume_id"] = stable_id
        resume_id = stable_id
        used_fallback_id = False
    else:
        resume_id = stable_fallback_resume_id(
            {
                "candidate_name": candidate_name,
                "title": title,
                "current_company": current_company,
                "location": location,
                "experiences": experiences[:2],
            }
        )
        used_fallback_id = True
    search_text = " ".join(
        [
            candidate_name,
            title,
            current_company,
            location,
            industry,
            *projects,
            *education,
            *work_experience_summaries,
            *work_summaries,
        ]
    )
    return ResumeCandidate(
        resume_id=resume_id,
        dedup_key=resume_id,
        used_fallback_id=used_fallback_id,
        age=28 + min(work_year or 5, 10),
        gender="unknown",
        now_location=location,
        work_year=work_year,
        expected_location=location,
        expected_job_category=title,
        expected_industry=industry,
        expected_salary="面议",
        active_status="active",
        job_state="open",
        education_summaries=education,
        work_experience_summaries=work_experience_summaries,
        project_names=projects,
        work_summaries=work_summaries,
        search_text=search_text,
        raw=raw_payload,
    )


def load_mock_resume_corpus() -> list[ResumeCandidate]:
    long_trace_text = (
        "Implemented trace-first agent workflow instrumentation across search, scoring, and reflection loops. "
        "Documented prompt hashes, normalization warnings, and event schemas for recruiter review. "
        "Delivered deterministic CLI workflows with controlled concurrency and audit-friendly JSONL artifacts. "
    ) * 8
    corpus = [
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
                    "duration": "2022-至今",
                    "summary": "Built Python agent loop with ReAct retrieval and structured reflection.",
                },
                {
                    "company": "RecruiterOS",
                    "title": "Backend Engineer",
                    "duration": "2019-2022",
                    "summary": "Implemented tracing, OpenAPI integration, and parallel resume scoring.",
                },
            ],
            work_summaries=[
                "python",
                "agent",
                "pydantic ai",
                "retrieval",
                "reflection",
                "trace",
                "resume matching",
                "cli",
                "ranking",
            ],
            skills=["python", "pydantic ai", "asyncio", "jsonl", "openapi"],
            languages=["中文", "英文"],
        ),
        _candidate(
            "mock-r002",
            candidate_name="Zhou Ming",
            title="",
            current_company="SearchLab",
            location="上海",
            work_year=7,
            industry="企业 SaaS",
            projects=["Candidate search pipeline", "Evaluation and reranking service"],
            education=["上海交通大学 软件工程 硕士"],
            experiences=[
                {
                    "company": "SearchLab",
                    "title": "LLM Search Engineer",
                    "duration": "2021-至今",
                    "summary": "Owned Python retrieval pipeline for talent search and deterministic reranking.",
                },
                {
                    "company": "DataMesh",
                    "title": "Backend Engineer",
                    "duration": "2018-2021",
                    "summary": "Designed ranking evidence summaries and observability baselines.",
                },
            ],
            work_summaries=["python", "retrieval", "ranking", "observability", "openapi"],
            skills=["python", "retrieval", "ranking", "observability"],
            headline="LLM Search Engineer",
        ),
        _candidate(
            "mock-r003",
            candidate_name="Wu Chen",
            title="Python Agent Platform Engineer",
            current_company="TalentMind",
            location="杭州",
            work_year=6,
            industry="人力资源科技",
            projects=["ReAct orchestration", "Prompt registry and trace store"],
            education=[],
            experiences=[
                {
                    "company": "TalentMind",
                    "title": "Python Agent Platform Engineer",
                    "duration": "2020-至今",
                    "summary": "Built pydantic-ai based agent orchestration for recruiter tooling.",
                },
                {
                    "company": "GraphOps",
                    "title": "Python Engineer",
                    "duration": "2018-2020",
                    "summary": "Added prompt hashing, JSONL events, and dedup-safe ranking.",
                },
            ],
            work_summaries=["python", "agent", "pydantic ai", "trace", "reflection"],
            skills=["python", "pydantic ai", "jsonl", "trace"],
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
                    "duration": "2019-至今",
                    "summary": "Integrated candidate search API adapters from validated specs.",
                },
                {
                    "company": "WorkAtlas",
                    "title": "Backend Engineer",
                    "duration": "2015-2019",
                    "summary": "Implemented stable dedup keys and local fallback filtering.",
                },
            ],
            work_summaries=["python", "cts", "openapi", "dedup", "filters"],
            skills=["python", "openapi", "httpx", "dedup"],
        ),
        _candidate(
            "mock-r005",
            candidate_name="Tian Yu",
            title="Python Workflow Engineer",
            current_company="SyncFlow",
            location="深圳",
            work_year=5,
            industry="企业软件",
            projects=["Async scoring fan-out", "CLI automation"],
            education=["华南理工大学 计算机 本科"],
            experiences=[
                {
                    "company": "SyncFlow",
                    "title": "Python Workflow Engineer",
                    "duration": "2021-至今",
                    "summary": "Implemented controlled concurrency for scoring branches.",
                },
                {
                    "company": "OpsTool",
                    "title": "Automation Engineer",
                    "duration": "2019-2021",
                    "summary": "Delivered synchronous CLI wrappers around async internals.",
                },
            ],
            work_summaries=["python", "asyncio", "parallel scoring", "cli", "fan-out", "fan-in"],
            skills=[],
        ),
        _candidate(
            "mock-r006",
            candidate_name="Xu Jie",
            title="AI Observability Engineer",
            current_company="TraceWorks",
            location="上海",
            work_year=6,
            industry="AI Infra",
            projects=["Trace lake", "LLM reflection diagnostics"],
            education=["同济大学 软件工程 本科"],
            experiences=[
                {
                    "company": "TraceWorks",
                    "title": "AI Observability Engineer",
                    "duration": "2020-至今",
                    "summary": "Built run tracing and structured logging for agent products.",
                },
                {
                    "company": "PromptOps",
                    "title": "Python Engineer",
                    "duration": "2018-2020",
                    "summary": "Shipped reflection diagnostics and prompt versioning.",
                },
            ],
            work_summaries=["trace", "logging", "reflection", "prompt hash", "observability", "python"],
            skills=["trace", "logging", "observability", "python"],
            full_text=long_trace_text,
        ),
        _candidate(
            "mock-r007",
            candidate_name="Qin Yue",
            title="Frontend Engineer",
            current_company="MallUI",
            location="上海",
            work_year=6,
            industry="电商",
            projects=["React dashboard", "Design system"],
            education=["南京大学 本科"],
            experiences=[
                {
                    "company": "MallUI",
                    "title": "Frontend Engineer",
                    "duration": "2020-至今",
                    "summary": "Built frontend dashboards and pixel-perfect web apps.",
                }
            ],
            work_summaries=["frontend", "react", "typescript", "ui", "dashboard"],
            skills=["react", "typescript", "design system"],
        ),
        _candidate(
            "mock-r008",
            candidate_name="Gao Peng",
            title="Java Agent Engineer",
            current_company="FactoryBot",
            location="苏州",
            work_year=8,
            industry="制造业软件",
            projects=["Java multi-agent platform"],
            education=["东南大学 本科"],
            experiences=[
                {
                    "company": "FactoryBot",
                    "title": "Java Agent Engineer",
                    "duration": "2019-至今",
                    "summary": "Built Java workflow orchestration for service bots.",
                }
            ],
            work_summaries=["java", "workflow", "agent", "orchestration"],
            skills=["java", "distributed systems"],
        ),
        _candidate(
            "mock-r009",
            candidate_name="Shen Ning",
            title="Python AI Engineer",
            current_company="InsightAI",
            location="远程",
            work_year=4,
            industry="AI 产品",
            projects=["Resume triage assistant"],
            education=["武汉大学 本科"],
            experiences=[
                {
                    "company": "InsightAI",
                    "title": "Python AI Engineer",
                    "duration": "2022-至今",
                    "summary": "Built Python LLM workflow for candidate review and retrieval tuning.",
                },
                {
                    "company": "OpsNLP",
                    "title": "Automation Engineer",
                    "duration": "2021-2022",
                    "summary": "Worked on structured scoring, trace logging, and CLI delivery.",
                },
            ],
            work_summaries=["python", "resume", "scoring", "agent", "retrieval", "trace", "cli"],
            skills=["python", "llm", "retrieval", "trace"],
            failure_mode="fail_once",
        ),
        _candidate(
            "mock-r010",
            candidate_name="Luo Han",
            title="Recruiting Sales Lead",
            current_company="HireGrowth",
            location="上海",
            work_year=10,
            industry="招聘服务",
            projects=["Sales ops"],
            education=["中山大学 本科"],
            experiences=[
                {
                    "company": "HireGrowth",
                    "title": "Recruiting Sales Lead",
                    "duration": "2017-至今",
                    "summary": "Led sales funnel for enterprise recruiting clients.",
                }
            ],
            work_summaries=["sales", "recruiting", "account management"],
            skills=["sales", "crm"],
        ),
        _candidate(
            "mock-r011",
            candidate_name="Fan Yi",
            title="HR Tech Search Engineer",
            current_company="TalentScope",
            location="上海",
            work_year=7,
            industry="人力资源科技",
            projects=["Resume recall tuning", "Trace-first evaluation"],
            education=["厦门大学 本科"],
            experiences=[
                {
                    "company": "TalentScope",
                    "title": "HR Tech Search Engineer",
                    "duration": "2020-至今",
                    "summary": "Built talent search retrieval and rerank loops.",
                },
                {
                    "company": "TalentScope",
                    "title": "Backend Engineer",
                    "duration": "2017-2020",
                    "summary": "Maintained trace-first review workflows for recruiters.",
                },
            ],
            work_summaries=["python", "resume search", "retrieval", "trace", "rerank", "recruiting"],
            skills=["python", "retrieval", "ranking", "trace"],
        ),
        _candidate(
            "mock-r012",
            candidate_name="Tang Rui",
            title="Algorithm Researcher",
            current_company="ModelLab",
            location="北京",
            work_year=5,
            industry="研究院",
            projects=["Ranking research"],
            education=["清华大学 博士"],
            experiences=[
                {
                    "company": "ModelLab",
                    "title": "Algorithm Researcher",
                    "duration": "2021-至今",
                    "summary": "Published papers on recommendation algorithms.",
                }
            ],
            work_summaries=["research", "algorithm", "paper", "ranking"],
            skills=["research", "ranking"],
        ),
        _candidate(
            "mock-r013",
            candidate_name="Ke Bo",
            title="Data Platform Engineer",
            current_company="DataRiver",
            location="杭州",
            work_year=6,
            industry="电商",
            projects=["Batch scoring"],
            education=["浙江工业大学 本科"],
            experiences=[
                {
                    "company": "DataRiver",
                    "title": "Data Platform Engineer",
                    "duration": "2019-至今",
                    "summary": "Built Python retrieval analytics and trace-backed batch scoring workflows.",
                }
            ],
            work_summaries=["python", "retrieval", "trace", "batch scoring", "cli"],
            skills=["python", "spark", "retrieval", "trace"],
            failure_mode="fail_always",
        ),
        _candidate(
            None,
            candidate_name="Meng Tao",
            title="Python Automation Engineer",
            current_company="PromptFlow",
            location="上海",
            work_year=None,
            industry="企业软件",
            projects=["Prompt-driven CLI automation"],
            education=["华东师范大学 本科"],
            experiences=[
                {
                    "company": "PromptFlow",
                    "title": "Python Automation Engineer",
                    "duration": "2021-至今",
                    "summary": "Implemented Python CLI tooling with structured outputs.",
                },
                {
                    "company": "PromptFlow",
                    "title": "Automation Engineer",
                    "duration": "2019-2021",
                    "summary": "Worked on workflow orchestration and evaluation loops.",
                },
            ],
            work_summaries=["python", "cli", "workflow", "evaluation", "structured output"],
            skills=["python", "cli", "workflow"],
        ),
        _candidate(
            "mock-r003",
            candidate_name="Wu Chen",
            title="Python Agent Platform Engineer",
            current_company="TalentMind",
            location="杭州",
            work_year=6,
            industry="人力资源科技",
            projects=["ReAct orchestration", "Prompt registry and trace store"],
            education=[],
            experiences=[
                {
                    "company": "TalentMind",
                    "title": "Python Agent Platform Engineer",
                    "duration": "2020-至今",
                    "summary": "Duplicate search hit for the same resume to test dedup.",
                }
            ],
            work_summaries=["python", "agent", "trace", "duplicate"],
            skills=["python", "trace"],
        ),
        _candidate(
            "mock-r005",
            candidate_name="Tian Yu",
            title="Python Workflow Engineer",
            current_company="SyncFlow",
            location="深圳",
            work_year=5,
            industry="企业软件",
            projects=["Async scoring fan-out", "CLI automation"],
            education=["华南理工大学 计算机 本科"],
            experiences=[
                {
                    "company": "SyncFlow",
                    "title": "Python Workflow Engineer",
                    "duration": "2021-至今",
                    "summary": "Duplicate mock record for fan-out testing.",
                }
            ],
            work_summaries=["python", "asyncio", "duplicate"],
            skills=[],
        ),
    ]
    return corpus
