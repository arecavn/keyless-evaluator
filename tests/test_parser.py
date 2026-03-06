"""Tests for the LLM response parser."""

import pytest
from keyless_evaluator.models import RelevanceScore, SearchResult
from keyless_evaluator.parser import parse_evaluation_response


SAMPLE_RESULTS = [
    SearchResult(id="r1", title="FastAPI Documentation"),
    SearchResult(id="r2", title="Django REST Framework"),
    SearchResult(id="r3", title="Chili Recipe"),
]


def test_parse_clean_json():
    raw = """[
        {"result_id": "r1", "score": 3, "reason_summary": "Perfect match.", "reason_detail": "FastAPI is exactly a python async web framework."},
        {"result_id": "r2", "score": 2, "reason_summary": "Relevant but sync.", "reason_detail": "Django REST works but is not async by default."},
        {"result_id": "r3", "score": 0, "reason_summary": "Not related.", "reason_detail": "Chili recipe has nothing to do with the query."}
    ]"""
    scores = parse_evaluation_response(raw, SAMPLE_RESULTS)
    assert len(scores) == 3
    assert scores[0].score == RelevanceScore.HIGHLY_RELEVANT
    assert scores[1].score == RelevanceScore.RELEVANT
    assert scores[2].score == RelevanceScore.IRRELEVANT


def test_parse_with_markdown_fences():
    raw = """Sure, here are my evaluations:
```json
[
    {"result_id": "r1", "score": 3, "reason_summary": "Great.", "reason_detail": "Details here."},
    {"result_id": "r2", "score": 1, "reason_summary": "Weak.", "reason_detail": "Details here."},
    {"result_id": "r3", "score": 0, "reason_summary": "Wrong.", "reason_detail": "Details here."}
]
```
Hope that helps!"""
    scores = parse_evaluation_response(raw, SAMPLE_RESULTS)
    assert scores[0].score == RelevanceScore.HIGHLY_RELEVANT
    assert scores[1].score == RelevanceScore.MARGINAL


def test_parse_score_clamped():
    raw = '[{"result_id": "r1", "score": 99, "reason_summary": "x", "reason_detail": "y"}]'
    scores = parse_evaluation_response(raw, [SAMPLE_RESULTS[0]])
    assert scores[0].score == RelevanceScore.HIGHLY_RELEVANT  # clamped to 3


def test_missing_result_gets_zero():
    raw = '[{"result_id": "r1", "score": 3, "reason_summary": "Great.", "reason_detail": "..."}]'
    # r2 and r3 are missing from the LLM output
    scores = parse_evaluation_response(raw, SAMPLE_RESULTS)
    assert len(scores) == 3
    assert scores[1].score == RelevanceScore.IRRELEVANT
    assert scores[2].score == RelevanceScore.IRRELEVANT


def test_invalid_json_raises():
    with pytest.raises(ValueError, match="valid JSON"):
        parse_evaluation_response("this is not json at all", SAMPLE_RESULTS)


def test_ndcg_computed():
    from keyless_evaluator.evaluators import _compute_ndcg
    from keyless_evaluator.models import ResultScore

    scores = [
        ResultScore(result_id="r1", title="A", score=RelevanceScore(3),
                    reason_summary="", reason_detail=""),
        ResultScore(result_id="r2", title="B", score=RelevanceScore(2),
                    reason_summary="", reason_detail=""),
        ResultScore(result_id="r3", title="C", score=RelevanceScore(0),
                    reason_summary="", reason_detail=""),
    ]
    ndcg = _compute_ndcg(scores)
    assert 0.0 < ndcg <= 1.0

    # Perfect ordering → nDCG = 1.0
    perfect = sorted(scores, key=lambda s: s.score.value, reverse=True)
    assert _compute_ndcg(perfect) == pytest.approx(1.0)
