"""Prompt templates for LLM evaluation."""

from __future__ import annotations
from models import EvaluationRequest


SYSTEM_PROMPT = """\
You are an expert search quality evaluator. Your task is to judge how relevant each search result is to the given query.

## Scoring Scale
Score each result on a 0–3 scale:
| Score | Label          | Meaning                                                        |
|-------|----------------|----------------------------------------------------------------|
|   3   | Highly Relevant | The result is a perfect or near-perfect answer to the query.  |
|   2   | Relevant        | The result addresses the query but may have minor gaps.       |
|   1   | Marginal        | The result is only tangentially related to the query.         |
|   0   | Irrelevant      | The result has no meaningful connection to the query.         |

## Output Format
You MUST return a JSON array — one object per result — in this exact schema:
```json
[
  {
    "result_id": "<id>",
    "score": <0|1|2|3>,
    "reason_summary": "<one sentence>",
    "reason_detail": "<2-4 sentence detailed justification>"
  },
  ...
]
```
Return ONLY valid JSON, no markdown fences, no extra commentary.\
"""

OUTPUT_FORMAT = """

## Output Format
You MUST return a JSON array — one object per result — in this exact schema:
```json
[
  {
    "result_id": "<id>",
    "score": <0|1|2|3>,
    "reason_summary": "<one sentence>",
    "reason_detail": "<2-4 sentence detailed justification>"
  },
  ...
]
```
Return ONLY valid JSON, no markdown fences, no extra commentary."""


def build_user_prompt(req: EvaluationRequest) -> str:
    """Build the user-turn prompt from an evaluation request."""
    lines: list[str] = []

    lines.append(f"## Input\n{req.input}")

    if req.query_context:
        lines.append(f"\n## Context\n{req.query_context}")

    lines.append("\n## Results to Evaluate")
    for i, result in enumerate(req.results, 1):
        lines.append(f"\n### Result {i}")
        lines.append(f"- **ID**: {result.id}")
        lines.append(f"- **Title**: {result.title}")
        if result.snippet:
            label = result.snippet_label or "Snippet"
            lines.append(f"- **{label}**: {result.snippet}")
        if result.url:
            lines.append(f"- **URL**: {result.url}")
        if result.metadata:
            for k, v in result.metadata.items():
                lines.append(f"- **{k.replace('_', ' ').title()}**: {v}")

    lines.append(
        "\n## Task\n"
        "Evaluate every result above against the query. "
        "Return a JSON array with one object per result (same order). "
        "No explanation outside the JSON."
    )

    return "\n".join(lines)
