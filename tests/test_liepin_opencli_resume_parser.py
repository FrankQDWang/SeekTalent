from __future__ import annotations

from seektalent.providers.liepin.opencli_resume_parser import build_liepin_opencli_detail_payload


def test_opencli_resume_parser_removes_page_chrome_and_keeps_resume_sections() -> None:
    raw_text = """
    首页
    搜索
    筛选
    推荐职位
    联系候选人
    查看联系方式
    下载简历
    当前职位：数据开发专家
    当前公司：恒生电子股份有限公司
    工作经历
    负责数据平台、ETL、数据治理和自动化任务建设。
    项目经历
    建设大规模数据仓库和日志分析平台。
    教育经历
    浙江大学 本科 计算机科学
    技能
    Python SQL Flink ClickHouse
    """

    payload = build_liepin_opencli_detail_payload(raw_text)

    assert payload["currentTitle"] == "数据开发专家"
    assert payload["currentCompany"] == "恒生电子股份有限公司"
    assert "工作经历" in payload["fullText"]
    assert "负责数据平台" in payload["fullText"]
    assert "浙江大学" in payload["fullText"]
    assert "联系候选人" not in payload["fullText"]
    assert "查看联系方式" not in payload["fullText"]
    assert "推荐职位" not in payload["fullText"]


def test_opencli_resume_parser_deduplicates_lines_without_semantic_filtering() -> None:
    raw_text = """
    当前职位：数据开发专家
    当前职位：数据开发专家
    工作经历
    负责数据平台。
    负责数据平台。
    低匹配但仍是候选人真实经历，应保留。
    """

    payload = build_liepin_opencli_detail_payload(raw_text)

    assert payload["fullText"].count("当前职位：数据开发专家") == 1
    assert payload["fullText"].count("负责数据平台。") == 1
    assert "低匹配但仍是候选人真实经历，应保留。" in payload["fullText"]
