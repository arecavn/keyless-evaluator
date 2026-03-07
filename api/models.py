"""Data models for search evaluation."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field


class FieldMapping(BaseModel):
    """
    Field mapping for dynamic JSON output.

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
    id_field: str = Field(default="id", description="Field name to use as the result ID.")
    title_field: str = Field(
        default="",
        description="Field name for the title. Auto-detected if empty (title, jobTitle, name, headline…).",
    )
    snippet_field: str = Field(
        default="",
        description="Field name for the snippet. Auto-detected if empty (snippet, jobDescription, description…).",
    )
    url_field: str = Field(
        default="",
        description="Field name for the URL. Auto-detected if empty.",
    )
    metadata_fields: list[str] = Field(
        default_factory=list,
        description="Extra fields to pass as metadata. Auto-selected if empty.",
    )


class RelevanceScore(IntEnum):
    """Relevance score levels (TREC-style 4-point scale)."""
    IRRELEVANT = 0
    MARGINAL = 1
    RELEVANT = 2
    HIGHLY_RELEVANT = 3

    @property
    def label(self) -> str:
        return {0: "Irrelevant", 1: "Marginal", 2: "Relevant", 3: "Highly Relevant"}[self.value]

    @property
    def color(self) -> str:
        return {0: "red", 1: "yellow", 2: "cyan", 3: "green"}[self.value]

    @property
    def emoji(self) -> str:
        return {0: "✗", 1: "~", 2: "✓", 3: "★"}[self.value]


class SearchResult(BaseModel):
    """A single item to be evaluated."""
    id: str | int = Field(description="Unique identifier")
    title: str = Field(description="Title or headline")
    snippet: str = Field(default="", description="Short excerpt or description")
    url: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResultScore(BaseModel):
    """LLM-generated score for a single result."""
    result_id: str | int
    title: str
    score: RelevanceScore
    reason_summary: str = Field(description="One-sentence summary")
    reason_detail: str = Field(description="Detailed justification")
    raw_response: str | None = Field(
        default=None, exclude=True,
        description="Raw LLM output (not serialized; written to logs/llm.log)",
    )


class EvaluationRequest(BaseModel):
    """Internal evaluation request passed to evaluator backends."""
    input: str = Field(description="The search query or evaluation criterion")
    prompt: str | None = Field(
        default=None,
        description="Custom evaluation prompt (replaces the default SYSTEM_PROMPT when provided)",
    )
    query_context: str | None = Field(default=None, description="Extra context about the query intent")
    results: list[SearchResult] = Field(description="The ordered list of results to evaluate")


class EvaluationResponse(BaseModel):
    """Full evaluation response from the LLM evaluator."""
    input: str
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


class EvaluationRequestBody(BaseModel):
    """
    Unified request body for ``POST /v1/evaluate``.

    ``output`` accepts **either**:

    - **string** — a plain text document/passage to score against ``input``
    - **JSON object / array** — raw response from any search API; field names are auto-detected

    ``prompt`` replaces the built-in evaluation instructions when provided (up to ~5 000 tokens).
    Use it to define your own scoring rubric for job search, candidate matching, product search, etc.

    ---

    **Plain-text example** (single document):

    ```json
    {
      "input": "Senior Python developer Hanoi",
      "output": "We are looking for a Python backend engineer with 3+ years experience...",
      "prompt": "Score how well this job posting matches the candidate profile. 3=perfect match, 0=no match."
    }
    ```

    **JSON search API example** (multiple results, auto-detected fields):

    ```json
    {
      "input": "remote python jobs",
      "output": {"data": [{"id": "j1", "jobTitle": "Python Dev", "jobDescription": "..."}]},
      "mapping": {"title_field": "jobTitle", "snippet_field": "jobDescription"}
    }
    ```

    **Custom mapping example**:

    ```json
    {
      "input": "remote python jobs",
      "output": { ...search API response... },
      "mapping": {
        "data_path": "data",
        "id_field": "id",
        "title_field": "jobTitle",
        "snippet_field": "jobDescription",
        "metadata_fields": ["company", "salary", "location"]
      },
      "max_results": 10
    }
    ```
    """
    input: str = Field(description="The search query or evaluation criterion")
    output: Any = Field(
        description=(
            "What to evaluate. "
            "A plain string (single document) OR a raw JSON object/array from a search API."
        ),
    )
    prompt: str | None = Field(
        default=None,
        description=(
            "Custom evaluation instructions — replaces the built-in scoring rubric. "
            "Describe your criteria here. Supports up to ~5 000 tokens. "
            "If omitted, the default TREC 0–3 relevance prompt is used."
        ),
    )
    mapping: FieldMapping = Field(
        default_factory=FieldMapping,
        description="Field mapping for JSON output. All fields are auto-detected if omitted.",
    )
    max_results: int = Field(
        default=20, ge=1, le=100,
        description="Max results to evaluate (JSON output only).",
    )
