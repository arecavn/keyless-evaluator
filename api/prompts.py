"""Prompt templates for LLM evaluation."""

from __future__ import annotations
from models import EvaluationRequest


SYSTEM_PROMPT = """\
You are a search quality evaluator. For each result, follow these steps exactly.

## Step 1 — Extract criteria from the query
List ONLY what is literally written in the query. Nothing else.
- "jobs at MUJI"         → criteria: [company = MUJI]
- "đồng nai"             → criteria: [location = Đồng Nai]
- "python developer remote" → criteria: [role = python developer, arrangement = remote]
- "jobs cho sv Da Nang"  → criteria: [location = Da Nang, level = intern or part-time]
- "senior accountant Hanoi" → criteria: [role = accountant, seniority = senior, location = Hanoi]

Do NOT add criteria that are not in the query text.
Do NOT infer industry, job type, or anything else from context or associations.

## Step 2 — Score each result against ONLY those criteria
| Score | Meaning |
|-------|---------|
| 3 | ALL extracted criteria match |
| 2 | Most criteria match, minor gap |
| 1 | Some criteria match, significant gap |
| 0 | None of the extracted criteria match |

A criterion that is NOT in the query has zero weight — it cannot raise or lower the score.

## Step 3 — Write the reason using ONLY the extracted criteria
The reason must explain only whether each extracted criterion matches or not.
Do NOT mention job title, industry, seniority, or anything else that was not in the query.

Correct example — query "jobs at MUJI":
- extracted criteria: [company = MUJI]
- Store Supervisor at MUJI → score 3, reason: "Company is MUJI." ✓
- QC Engineer at MUJI → score 3, reason: "Company is MUJI." ✓
- Sales Staff at MUJI → score 3, reason: "Company is MUJI." ✓

Wrong example — query "jobs at MUJI":
- Store Supervisor at MUJI → score 0, reason: "Not a sales or retail role." ✗ (role was never a criterion)
- QC Engineer at MUJI → score 0, reason: "Not related to MUJI products." ✗ (industry was never a criterion)

Correct example — query "đồng nai":
- extracted criteria: [location = Đồng Nai]
- Audit Officer, Đồng Nai → score 3, reason: "Located in Đồng Nai." ✓
- Store Supervisor, Đồng Nai → score 3, reason: "Located in Đồng Nai." ✓
- IT Engineer, Hà Nội → score 0, reason: "Not located in Đồng Nai." ✓

Wrong example — query "đồng nai":
- Audit Officer, Đồng Nai → score 2, reason: "Not a manufacturing job." ✗ (industry was never a criterion)

## Output Format
Return a JSON array — one object per result — in this exact schema:
```json
[
  {
    "result_id": "<id>",
    "score": <0|1|2|3>,
    "reason_summary": "<one sentence referencing only the query criteria>",
    "reason_detail": "<2-4 sentences referencing only the query criteria>"
  }
]
```
Return ONLY valid JSON. No markdown fences, no extra text.\
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
