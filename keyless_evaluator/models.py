"""Data models for search evaluation."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field


class FieldMapping(BaseModel):
    """
    Field mapping for dynamic JSON input (used by /v1/evaluate/raw).

    All fields are optional — omit any you don't need to override.
    Dot-notation is supported for nested paths, e.g. ``"hits.hits"``.
    """
    data_path: str = Field(
        default="data",
        description=(
            "Dot-notation path to the array of result items inside the raw JSON. "
            "E.g. 'data', 'hits.hits', 'results.items'. "
            "Use '' (empty string) if the root itself is the array."
        ),
    )
    id_field: str = Field(
        default="id",
        description="Field name inside each item to use as the result ID.",
    )
    title_field: str = Field(
        default="",
        description=(
            "Field name inside each item to use as the title. "
            "If empty, auto-detected from common names: "
            "title, jobTitle, name, headline, subject, label."
        ),
    )
    snippet_field: str = Field(
        default="",
        description=(
            "Field name inside each item to use as the snippet/description. "
            "If empty, auto-detected from common names: "
            "snippet, jobDescription, description, summary, body, content, excerpt."
        ),
    )
    url_field: str = Field(
        default="",
        description=(
            "Field name inside each item to use as the URL. "
            "If empty, auto-detected from common URL-like field names."
        ),
    )
    metadata_fields: list[str] = Field(
        default_factory=list,
        description=(
            "List of additional fields to include as metadata for the LLM. "
            "If empty, a sensible selection of scalar fields is included automatically."
        ),
    )


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
    """Full evaluation response from the LLM evaluator."""
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


class RawEvaluationRequest(BaseModel):
    """
    Dynamic-input evaluation request.

    Pass the raw JSON response body from **any** search API directly — no reformatting needed.
    The adapter will extract results using ``mapping`` and convert them to ``SearchResult`` objects.

    Example — copy your search API response verbatim into ``raw``:

    .. code-block:: json

        {
          "query": "remote jobs",
          "raw": { ...your full search API response here... },
          "mapping": {
            "data_path": "data",
            "id_field": "id",
            "title_field": "jobTitle",
            "snippet_field": "jobDescription",
            "metadata_fields": ["company", "salary", "location", "employmentTypeEn"]
          }
        }

    All ``mapping`` fields are optional — if omitted, common field names are auto-detected.
    """
    query: str = Field(description="The search query to evaluate against.")
    query_context: str | None = Field(
        default=None,
        description="Additional context about the query intent (optional).",
    )
    raw: Any = Field(
        description=(
            "The raw JSON response body from your search API. "
            "Can be an object (with a nested array) or a bare array."
        ),
    )
    mapping: FieldMapping = Field(
        default_factory=FieldMapping,
        description="Field mapping configuration. All fields have sensible defaults.",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of results to evaluate (takes the first N from the array).",
    )

