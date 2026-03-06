"""Data models for search evaluation."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field


class RelevanceScore(IntEnum):
    """Relevance score levels (TREC-style 4-point scale)."""
    IRRELEVANT = 0       # No connection to the query
    MARGINAL = 1         # Slightly related, mostly misses intent
    RELEVANT = 2         # Answers query partially / mostly
    HIGHLY_RELEVANT = 3  # Perfect match

    @property
    def label(self) -> str:
        return {
            0: "Irrelevant",
            1: "Marginal",
            2: "Relevant",
            3: "Highly Relevant",
        }[self.value]

    @property
    def color(self) -> str:
        return {
            0: "red",
            1: "yellow",
            2: "cyan",
            3: "green",
        }[self.value]

    @property
    def emoji(self) -> str:
        return {0: "✗", 1: "~", 2: "✓", 3: "★"}[self.value]


class SearchResult(BaseModel):
    """A single search result item to be evaluated."""
    id: str | int = Field(description="Unique identifier for the result (e.g., document id, URL, job id)")
    title: str = Field(description="Title or headline of the result")
    snippet: str = Field(default="", description="Short excerpt or description")
    url: str | None = Field(default=None, description="URL if applicable")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Extra context fields")


class ResultScore(BaseModel):
    """LLM-generated score for a single search result."""
    result_id: str | int
    title: str
    score: RelevanceScore
    reason_summary: str = Field(description="One-sentence summary of why this score was given")
    reason_detail: str = Field(description="Detailed explanation of the relevance judgment")
    raw_response: str | None = Field(default=None, description="Raw LLM output for this item")


class EvaluationRequest(BaseModel):
    """A full evaluation request: query + list of results."""
    query: str = Field(description="The search query being evaluated")
    query_context: str | None = Field(default=None, description="Additional context about the query intent")
    results: list[SearchResult] = Field(description="The ordered list of search results to evaluate")


class EvaluationResponse(BaseModel):
    """Full evaluation response from the LLM oracle."""
    query: str
    model: str
    provider: str
    scores: list[ResultScore]
    ndcg: float | None = Field(default=None, description="nDCG@k if computable")
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def average_score(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.score.value for s in self.scores) / len(self.scores)

    def to_table_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "rank": i + 1,
                "id": s.result_id,
                "title": s.title[:60] + ("…" if len(s.title) > 60 else ""),
                "score": s.score.value,
                "label": s.score.label,
                "summary": s.reason_summary,
            }
            for i, s in enumerate(self.scores)
        ]
