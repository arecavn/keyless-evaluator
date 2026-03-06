---
name: api-design-standards
description: >
  Standards for designing high-quality REST API input/output schemas, validation rules,
  versioning, error formats, and OpenAPI documentation. Use this skill when designing or
  reviewing API endpoints, request/response models, error handling, or pagination patterns.
  Applies to any FastAPI, Flask, or REST service in this project.
---

# API Design Standards

## Core Principles

1. **Explicit contracts** — every endpoint has a versioned, documented schema
2. **Fail fast, fail clearly** — validate at the boundary; return structured errors always
3. **Idempotency** — GET and safe methods must be side-effect-free; POST evaluate is idempotent on same input
4. **Backward compatibility** — never break existing consumers; evolve via new fields or new versions

---

## URL & Versioning

```
/v1/evaluate        ← current stable
/v2/evaluate        ← future breaking changes go here
/health             ← no versioning, always stable
```

- Version prefix in path: `/v{N}/...`
- Never remove or rename fields in an existing version — add new optional fields instead
- Announce deprecation 90 days before removal via `Deprecation` and `Sunset` response headers

---

## Request Schema Rules

### Required fields — refuse if missing with `422`

| Field | Type | Validation |
|---|---|---|
| `query` | `string` | 1–2000 chars, stripped of leading/trailing whitespace |
| `results` | `array[SearchResult]` | 1–100 items |
| `results[].id` | `string \| int` | unique within the array |
| `results[].title` | `string` | 1–500 chars |

### Optional fields — safe defaults if absent

| Field | Default | Max |
|---|---|---|
| `query_context` | `null` | 1000 chars |
| `results[].snippet` | `""` | 2000 chars |
| `results[].url` | `null` | 2048 chars (RFC 3986) |
| `results[].metadata` | `{}` | 50 keys, values < 500 chars each |

### Query parameters

| Param | Default | Allowed values |
|---|---|---|
| `provider` | `gemini` | `gemini`, `openai`, `anthropic`, `chatgpt_web` |
| `model` | provider default | any string (validated by provider) |

---

## Response Schema Rules

Always return the same envelope shape. Never return raw strings or bare arrays at the top level.

```json
{
  "query": "string",
  "model": "string",
  "provider": "string",
  "ndcg": 0.9123,
  "prompt_tokens": 512,
  "completion_tokens": 256,
  "scores": [
    {
      "result_id": "string | int",
      "title": "string",
      "score": 0,
      "reason_summary": "One sentence.",
      "reason_detail": "2-4 sentences.",
      "raw_response": null
    }
  ]
}
```

- `ndcg` — always a float 0.0–1.0, or `null` if not computable (empty scores)
- `scores` — same order as input `results`; length always equals input `results` length
- `raw_response` — `null` in production responses by default; expose only for debugging

---

## Error Response Format

**Always** return JSON even for errors. Never return plain text or HTML error pages.

```json
{
  "detail": "Human-readable error message",
  "code": "VALIDATION_ERROR",
  "field": "results[0].title"
}
```

| HTTP Status | When to use |
|---|---|
| `400` | Client malformed request (wrong type, missing field) |
| `422` | Validation failed (field exists but invalid value) |
| `429` | Rate limit exceeded |
| `500` | Internal / LLM failure — never leak stack traces |
| `503` | Provider unavailable (LLM API down, browser cannot start) |

FastAPI auto-returns `422` for Pydantic validation failures — this is correct and expected.

---

## OpenAPI / Docs Standards

- Every endpoint has a `summary`, `description`, and `response_model`
- Every field in Pydantic models has a `description=` in `Field(...)`
- Tag endpoints: `evaluate`, `health`
- Provide at least one working `example` in the schema for `/v1/evaluate`

---

## Pagination (future growth)

When result sets can exceed 100 items, add cursor-based pagination:

```json
{
  "data": [...],
  "next_cursor": "opaque_string_or_null",
  "has_more": false
}
```

Do not use offset pagination — it breaks under concurrent writes and is non-deterministic.

---

## Content Negotiation

- Always accept `Content-Type: application/json`
- Always respond with `Content-Type: application/json; charset=utf-8`
- Reject non-JSON content types with `415 Unsupported Media Type`
