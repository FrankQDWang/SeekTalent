from __future__ import annotations

from seektalent.candidate_feedback.familying import canonicalize_surface, should_merge_spans
from seektalent.candidate_feedback.span_models import CandidateSpan


def _span(surface: str) -> CandidateSpan:
    return CandidateSpan.build(
        source_resume_id="resume-1",
        source_field="evidence",
        source_text_index=0,
        start_char=0,
        end_char=len(surface),
        raw_surface=surface,
        normalized_surface=surface,
        model_label="technical_phrase",
        model_score=0.99,
        extractor_schema_version="span-extractor-v1",
    )


def test_canonicalize_surface_collapses_separator_variants() -> None:
    assert canonicalize_surface("Flink CDC") == "flinkcdc"
    assert canonicalize_surface("Flink-CDC") == "flinkcdc"
    assert canonicalize_surface("Flink_CDC") == "flinkcdc"


def test_should_merge_spans_merges_flink_cdc_camel_case_variants() -> None:
    merged, reason = should_merge_spans(
        _span("Flink CDC"),
        _span("FlinkCDC"),
        embedding_similarity=0.97,
    )

    assert merged is True
    assert reason == "canonical_surface_match"


def test_should_merge_spans_merges_separator_and_case_variants() -> None:
    merged, reason = should_merge_spans(
        _span("React Native"),
        _span("react-native"),
        embedding_similarity=0.98,
    )

    assert merged is True
    assert reason == "canonical_surface_match"


def test_should_merge_spans_rejects_java_vs_javascript() -> None:
    merged, reason = should_merge_spans(
        _span("Java"),
        _span("JavaScript"),
        embedding_similarity=0.99,
    )

    assert merged is False
    assert reason == "confusable_pair_java_javascript"


def test_should_merge_spans_rejects_react_vs_react_native() -> None:
    merged, reason = should_merge_spans(
        _span("React"),
        _span("React Native"),
        embedding_similarity=0.99,
    )

    assert merged is False
    assert reason == "confusable_pair_react_native"


def test_should_merge_spans_rejects_data_warehouse_vs_data_platform() -> None:
    merged, reason = should_merge_spans(
        _span("数据仓库"),
        _span("数据平台"),
        embedding_similarity=0.99,
    )

    assert merged is False
    assert reason == "confusable_pair_data_platform"
