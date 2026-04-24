from seektalent.models import HardConstraintSlots, QueryTermCandidate, SchoolTypeRequirement
from seektalent.retrieval.query_compiler import compile_query_term_pool


def _by_term(pool):
    return {item.term: item for item in pool}


def test_query_term_candidate_defaults_search_metadata_from_category() -> None:
    candidate = QueryTermCandidate(
        term="python",
        source="job_title",
        category="role_anchor",
        priority=1,
        evidence="title",
        first_added_round=0,
    )

    assert candidate.retrieval_role == "role_anchor"
    assert candidate.queryability == "admitted"
    assert candidate.family == "role.python"


def test_query_compiler_strips_title_suffix_and_blocks_dirty_terms() -> None:
    pool = compile_query_term_pool(
        job_title="AI Agent工程师",
        title_anchor_terms=["AI Agent工程师"],
        title_anchor_term="AI Agent工程师",
        jd_query_terms=["任务拆解", "AgentLoop调优", "211", "LangChain"],
        notes_query_terms=["Python"],
        hard_constraints=HardConstraintSlots(
            school_type_requirement=SchoolTypeRequirement(canonical_types=["211"], raw_text="211")
        ),
    )
    terms = _by_term(pool)

    assert "AI Agent工程师" not in terms
    assert terms["AI Agent"].retrieval_role == "primary_role_anchor"
    assert terms["AI Agent"].queryability == "admitted"
    assert terms["AI Agent"].family == "role.aiagent"
    assert terms["任务拆解"].queryability == "score_only"
    assert terms["任务拆解"].active is False
    assert terms["AgentLoop调优"].queryability == "blocked"
    assert terms["211"].queryability == "filter_only"
    assert terms["211"].family == "constraint.school_type"
    assert terms["LangChain"].queryability == "admitted"
    assert terms["LangChain"].family == "domain.langchain"
    assert terms["Python"].queryability == "admitted"
    assert terms["Python"].active is True
    assert terms["Python"].family == "domain.python"


def test_query_compiler_drops_generic_separator_prefix_from_role_anchor() -> None:
    pool = compile_query_term_pool(
        job_title="千问-AI Agent工程师",
        title_anchor_terms=["千问-AI Agent工程师"],
        title_anchor_term="千问-AI Agent工程师",
        jd_query_terms=["Java"],
        notes_query_terms=[],
    )
    terms = _by_term(pool)

    assert "千问-AI Agent" not in terms
    assert terms["AI Agent"].retrieval_role == "primary_role_anchor"
    assert terms["AI Agent"].queryability == "admitted"
    assert terms["AI Agent"].family == "role.aiagent"


def test_query_compiler_keeps_slash_domain_role_anchor_together() -> None:
    pool = compile_query_term_pool(
        job_title="搜索/推荐算法工程师",
        title_anchor_terms=["搜索/推荐算法工程师"],
        title_anchor_term="搜索/推荐算法工程师",
        jd_query_terms=["Java"],
        notes_query_terms=[],
    )

    assert pool[0].term == "搜索/推荐"


def test_query_compiler_does_not_add_broad_domain_terms_for_llm_algorithm_title() -> None:
    pool = compile_query_term_pool(
        job_title="LLM Agent算法工程师",
        title_anchor_terms=["Agent算法工程师"],
        title_anchor_term="Agent算法工程师",
        jd_query_terms=["LLM Agent", "Agent框架"],
        notes_query_terms=["Python"],
    )
    terms = _by_term(pool)

    assert pool[0].term == "Agent"
    assert terms["Agent"].retrieval_role == "primary_role_anchor"
    assert "大模型" not in terms
    assert terms["LLM Agent"].retrieval_role == "domain_context"
    assert terms["LLM Agent"].family == "domain.llmagent"
    assert terms["Python"].queryability == "admitted"


def test_query_compiler_deduplicates_terms_and_keeps_generic_families() -> None:
    pool = compile_query_term_pool(
        job_title="Python 工程师",
        title_anchor_terms=["Python"],
        title_anchor_term="Python",
        jd_query_terms=["LangChain", "langchain"],
        notes_query_terms=["RAG"],
    )

    assert [item.term for item in pool].count("LangChain") == 1
    assert _by_term(pool)["LangChain"].family == "domain.langchain"
    assert _by_term(pool)["RAG"].queryability == "admitted"


def test_query_compiler_accepts_title_anchor_terms_interface() -> None:
    pool = compile_query_term_pool(
        job_title="Python 工程师",
        title_anchor_terms=["Python", "Backend Engineer"],
        jd_query_terms=["LangChain"],
        notes_query_terms=["RAG"],
    )

    assert pool[0].term == "Python"
    assert pool[0].retrieval_role == "primary_role_anchor"
    assert pool[1].term == "Backend Engineer"
    assert pool[1].retrieval_role == "secondary_title_anchor"


def test_query_compiler_emits_primary_and_secondary_title_anchors() -> None:
    pool = compile_query_term_pool(
        job_title="AI/投研工程师",
        title_anchor_terms=["AI", "投研"],
        jd_query_terms=["机器学习"],
        notes_query_terms=[],
    )

    assert pool[0].term == "AI"
    assert pool[0].retrieval_role == "primary_role_anchor"
    assert pool[1].term == "投研"
    assert pool[1].retrieval_role == "secondary_title_anchor"


def test_query_compiler_keeps_single_title_anchor_when_only_one_is_present() -> None:
    pool = compile_query_term_pool(
        job_title="AI工程师",
        title_anchor_terms=["AI"],
        jd_query_terms=["机器学习"],
        notes_query_terms=[],
    )

    anchors = [item for item in pool if item.retrieval_role in {"primary_role_anchor", "secondary_title_anchor"}]
    assert [item.term for item in anchors] == ["AI"]
    assert anchors[0].retrieval_role == "primary_role_anchor"


def test_query_compiler_admits_explicit_domain_notes_terms_only() -> None:
    pool = compile_query_term_pool(
        job_title="医疗器械工程师",
        title_anchor_terms=["医疗器械"],
        jd_query_terms=["销售渠道"],
        notes_query_terms=["人工耳蜗", "沟通能力"],
    )
    terms = _by_term(pool)

    assert terms["人工耳蜗"].queryability == "admitted"
    assert terms["人工耳蜗"].retrieval_role == "domain_context"
    assert terms["人工耳蜗"].active is True
    assert "沟通能力" not in terms
