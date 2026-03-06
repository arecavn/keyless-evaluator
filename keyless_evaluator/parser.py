"""Parse and validate LLM JSON responses into typed ResultScore objects."""

from __future__ import annotations

import json
import re

from keyless_evaluator.models import RelevanceScore, ResultScore, SearchResult


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if the LLM wrapped its response."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1)
    return text.strip()


def _find_json_array(text: str) -> str:
    """Try to find a JSON array in the text, even if there's surrounding noise."""
    text = _strip_fences(text)
    # Look for the first '[' … last ']'
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_evaluation_response(
    raw: str,
    results: list[SearchResult],
) -> list[ResultScore]:
    """
    Parse the raw LLM output into a list of ResultScore objects.

    Falls back gracefully: if a result is missing from the LLM output it gets
    score=0 with a note, and unknown extra scores are discarded.
    """
    json_text = _find_json_array(raw)

    try:
        items: list[dict] = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM did not return valid JSON.\n"
            f"--- Raw output ---\n{raw}\n"
            f"--- Parse error ---\n{exc}"
        ) from exc

    # Build a lookup by result_id (string-coerced for safety)
    parsed: dict[str, dict] = {str(item.get("result_id", "")): item for item in items}

    scored: list[ResultScore] = []
    for result in results:
        key = str(result.id)
        item = parsed.get(key)

        if item is None:
            # LLM skipped this result — give it a score of 0
            scored.append(
                ResultScore(
                    result_id=result.id,
                    title=result.title,
                    score=RelevanceScore(0),
                    reason_summary="LLM did not evaluate this result.",
                    reason_detail="No evaluation data returned from the model.",
                    raw_response=raw,
                )
            )
            continue

        raw_score = int(item.get("score", 0))
        raw_score = max(0, min(3, raw_score))  # clamp

        scored.append(
            ResultScore(
                result_id=result.id,
                title=result.title,
                score=RelevanceScore(raw_score),
                reason_summary=item.get("reason_summary", "No summary provided."),
                reason_detail=item.get("reason_detail", "No detail provided."),
                raw_response=raw,
            )
        )

    return scored
