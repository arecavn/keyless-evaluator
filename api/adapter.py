"""
Dynamic JSON input adapter for /v1/evaluate/raw.

Converts any search API response body into a list of SearchResult objects
by resolving dot-notation paths and auto-detecting common field names.
"""

from __future__ import annotations

from typing import Any

from models import FieldMapping, SearchResult


# ---------------------------------------------------------------------------
# Common field name candidates (tried in priority order)
# ---------------------------------------------------------------------------

_TITLE_CANDIDATES = [
    "title", "jobTitle", "job_title", "name", "headline",
    "subject", "label", "displayName", "display_name",
    "productName", "product_name", "header",
]

_SNIPPET_CANDIDATES = [
    "snippet", "jobDescription", "job_description", "jobDetail", "job_detail",
    "jobContent", "job_content", "jobRequirement", "job_requirement",
    "description", "summary", "body", "content", "excerpt", "detail", "overview",
    "abstract", "text", "shortDescription", "short_description",
]

_URL_CANDIDATES = [
    "url", "link", "href", "webUrl", "web_url", "detailUrl",
    "detail_url", "canonicalUrl", "canonical_url", "pageUrl",
]

# Fields we never include in metadata (they're already mapped or are internal)
_SKIP_IN_METADATA = {"_score", "_id", "_index", "_source"}

# Max metadata field count — no value length cap (preserve original content)
_MAX_METADATA_FIELDS = 20
_MAX_METADATA_VALUE_LEN = None  # no truncation


# ---------------------------------------------------------------------------
# Dot-path resolver
# ---------------------------------------------------------------------------

def _resolve_path(obj: Any, path: str) -> Any:
    """
    Resolve a dot-notation path within a nested dict/list.

    Examples:
        _resolve_path({"data": [...]}, "data")       -> [...]
        _resolve_path({"hits": {"hits": [...]}}, "hits.hits") -> [...]
        _resolve_path([...], "")                     -> [...]  (root is the array)
    """
    if not path:
        return obj
    for key in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list):
            # Try to parse as integer index
            try:
                obj = obj[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if obj is None:
            return None
    return obj


# ---------------------------------------------------------------------------
# Auto-detect helpers
# ---------------------------------------------------------------------------

def _first_matching_key(item: dict, candidates: list[str]) -> str:
    """Return the first candidate key that exists and has a non-empty value."""
    for key in candidates:
        val = item.get(key)
        if val is not None and str(val).strip():
            return key
    return ""


def _scalar_value(val: Any) -> str | None:
    """
    Convert a value to a short string suitable for metadata.
    Returns None if the value is too complex or empty.

    Handles:
    - Scalars: returned as-is (truncated)
    - Flat lists: joined with ", "
    - List-of-dicts with a "name" key: joins the name values
      e.g. [{"id": 1, "name": "Graphic Design"}, ...] → "Graphic Design, ..."
    - Pure nested dicts or other complex types: dropped (returns None)
    """
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        s = str(val).strip()
        return s if s else None
    if isinstance(val, list):
        if val and isinstance(val[0], dict):
            # List-of-dicts: extract "name" field (e.g. skills array)
            names = [str(item["name"]).strip() for item in val if isinstance(item, dict) and item.get("name")]
            return ", ".join(names) if names else None
        # Flat lists of scalars (e.g. ["Hà Nội", "Hồ Chí Minh"])
        flat = [str(v) for v in val if isinstance(v, (str, int, float, bool))]
        return ", ".join(flat) if flat else None
    if isinstance(val, dict):
        # Multilingual dicts like {"vi": "...", "en": "..."} — pick first non-empty string value
        for v in val.values():
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _auto_metadata(item: dict, exclude_keys: set[str]) -> dict[str, Any]:
    """
    Build a metadata dict from scalar/simple-list fields of an item,
    excluding keys that are already mapped to id/title/snippet/url.
    """
    meta: dict[str, Any] = {}
    for key, val in item.items():
        if key in exclude_keys or key in _SKIP_IN_METADATA:
            continue
        if len(meta) >= _MAX_METADATA_FIELDS:
            break
        sv = _scalar_value(val)
        if sv is not None:
            meta[key] = sv
    return meta


# ---------------------------------------------------------------------------
# Public adapter function
# ---------------------------------------------------------------------------

def adapt_raw_input(
    raw: Any,
    mapping: FieldMapping,
    max_results: int = 20,
) -> list[SearchResult]:
    """
    Convert a raw search API response into a list of SearchResult objects.

    Args:
        raw: The parsed JSON from the search API (dict or list).
        mapping: Field mapping configuration (all fields have defaults).
        max_results: Maximum number of results to extract.

    Returns:
        List of SearchResult objects ready for evaluation.

    Raises:
        ValueError: If no result array can be found or extracted.
    """
    # Step 1 — find the results array
    items = _resolve_path(raw, mapping.data_path)

    # Fallback: if the resolved value isn't a list, try common top-level array keys
    if not isinstance(items, list):
        for fallback_key in ("data", "results", "hits", "items", "docs", "records", "jobs"):
            candidate = _resolve_path(raw, fallback_key)
            if isinstance(candidate, list) and candidate:
                items = candidate
                break

    # Last resort: if raw itself is a list
    if not isinstance(items, list) and isinstance(raw, list):
        items = raw

    if not isinstance(items, list):
        raise ValueError(
            f"Could not find a result array. "
            f"Tried data_path='{mapping.data_path}' and common fallback keys. "
            f"Please set 'mapping.data_path' to the key containing the results array."
        )

    if not items:
        raise ValueError("The results array is empty.")

    items = items[:max_results]

    # Step 2 — auto-detect field names from the first item
    first = items[0] if isinstance(items[0], dict) else {}

    id_field = mapping.id_field or "id"
    title_field = mapping.title_field or _first_matching_key(first, _TITLE_CANDIDATES)
    snippet_field = mapping.snippet_field or _first_matching_key(first, _SNIPPET_CANDIDATES)
    url_field = mapping.url_field or _first_matching_key(first, _URL_CANDIDATES)

    if not title_field:
        raise ValueError(
            "Could not auto-detect a title field. "
            f"Set 'mapping.title_field' explicitly. Available keys: {list(first.keys())[:10]}"
        )

    # Step 3 — build SearchResult objects
    results: list[SearchResult] = []
    mapped_keys = {id_field, title_field, snippet_field, url_field}

    explicit_meta_fields = set(mapping.metadata_fields)

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        result_id = item.get(id_field, f"result_{i + 1}")
        title = str(item.get(title_field, "")).strip() or f"Result {i + 1}"
        # snippet: handle plain strings and multilingual dicts {"vi": "...", "en": "..."}
        _snippet_raw = item.get(snippet_field) if snippet_field else None
        if isinstance(_snippet_raw, dict):
            snippet = next((v.strip() for v in _snippet_raw.values() if isinstance(v, str) and v.strip()), "")
        else:
            snippet = str(_snippet_raw or "").strip()
        url_val = item.get(url_field) if url_field else None
        url = str(url_val).strip() if url_val else None

        # Build metadata
        if explicit_meta_fields:
            meta = {
                k: _scalar_value(item.get(k)) or ""
                for k in explicit_meta_fields
                if k in item
            }
        else:
            meta = _auto_metadata(item, exclude_keys=mapped_keys)

        results.append(SearchResult(
            id=result_id,
            title=title,
            snippet=snippet,
            snippet_label=snippet_field or "Snippet",
            url=url,
            metadata=meta,
        ))

    if not results:
        raise ValueError("No valid result items could be extracted from the array.")

    return results
