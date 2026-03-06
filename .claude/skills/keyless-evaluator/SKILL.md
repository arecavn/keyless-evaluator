---
name: keyless-evaluator
description: >
  LLM-as-judge search result evaluator. Scores search results 0-3 (Irrelevant → Highly Relevant)
  using Gemini, OpenAI, Anthropic, or anonymous ChatGPT Web. Runs with `uv run`.
  Supports standard {id, title, snippet} input AND dynamic raw JSON from any search API via
  /v1/evaluate/raw with auto field detection. Use this skill when: evaluating search quality,
  running LLM-as-judge scoring, ranking results, computing nDCG, or integrating the REST API.
---

# Keyless Evaluator — Agent Skill

## Quick Reference

```bash
# Sync deps (Python 3.13, uv)
uv sync

# CLI
uv run keyless-eval --help
uv run keyless-eval eval -q "query" -f results.json               # Gemini (default, free)
uv run keyless-eval eval -q "query" -f results.json -p chatgpt_web # no key needed
uv run keyless-eval eval -q "query" -f results.json -p anthropic
uv run keyless-eval providers

# HTTP server
uv run keyless-eval serve   # → http://127.0.0.1:8000  docs: /docs

# Tests
uv run pytest tests/ -v
```

> **macOS sandbox**: set `UV_PROJECT_ENVIRONMENT=/tmp/keyless-eval-venv` if `.venv` fails.

## Architecture

```
keyless_evaluator/
├── cli.py          # Typer CLI (eval, detail, example, providers, serve)
├── models.py       # Pydantic: RelevanceScore, SearchResult, EvaluationRequest/Response,
│                   #           FieldMapping, RawEvaluationRequest
├── prompts.py      # SYSTEM_PROMPT + build_user_prompt
├── parser.py       # Parse raw LLM JSON → list[ResultScore], robust fence stripping
├── evaluators.py   # Backends: GeminiEvaluator, OpenAIEvaluator, AnthropicEvaluator,
│                   #           ChatGPTWebEvaluator + factory get_evaluator()
├── adapter.py      # Dynamic raw JSON adapter: dot-path resolver, auto field detection
├── renderer.py     # Rich terminal: tables, detail panels, nDCG stats
└── server.py       # FastAPI: POST /v1/evaluate, POST /v1/evaluate/raw, GET /health
```

## Providers

| Provider | Key Needed | Default Model | Notes |
|---|---|---|---|
| `gemini` *(default)* | `GEMINI_API_KEY` | `gemini-2.0-flash` | Free 1500 req/day |
| `chatgpt_web` | None | `auto` | Anonymous ChatGPT via Playwright |
| `openai` | `OPENAI_API_KEY` | `gpt-4o` | OpenAI API |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-3-5-haiku-20241022` | Anthropic Claude |

## API Endpoints

### POST /v1/evaluate — standard structured input
```json
{
  "query": "remote jobs",
  "results": [
    {"id": "r1", "title": "...", "snippet": "...", "url": "...", "metadata": {}}
  ]
}
```

### POST /v1/evaluate/raw — paste any search API response directly
```json
{
  "query": "remote jobs",
  "max_results": 10,
  "raw": { ...any search API response body... },
  "mapping": {
    "data_path": "data",
    "id_field": "id",
    "title_field": "jobTitle",
    "snippet_field": "jobDescription",
    "metadata_fields": ["company", "salary", "location", "employmentTypeEn"]
  }
}
```
All `mapping` fields are **optional** — auto-detected from common names if omitted.
Auto-detected array keys: `data`, `results`, `hits`, `items`, `docs`, `records`, `jobs`.
Auto-detected title candidates: `title`, `jobTitle`, `name`, `headline`, `subject`, `label`.
Auto-detected snippet candidates: `snippet`, `jobDescription`, `description`, `summary`, `body`.

## adapter.py — Key Functions

- `adapt_raw_input(raw, mapping, max_results)` → `list[SearchResult]`
- `_resolve_path(obj, "dot.notation.path")` → nested value
- `_scalar_value(val)` → flattens lists (`["Hà Nội", "HCM"]` → `"Hà Nội, HCM"`)
- `_auto_metadata(item, exclude_keys)` → picks best scalar fields, max 12

## Adding a New Provider

1. Create class in `evaluators.py` extending `BaseEvaluator`
2. Implement `async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse`
3. Use `self._build_response(request, scores)` to wrap results
4. Add to `PROVIDER_MAP` and `_DEFAULT_MODELS`

## Vercel Deployment

```bash
vercel env add GEMINI_API_KEY
vercel env add ALLOWED_ORIGINS   # comma-separated CORS origins
vercel deploy --prod
```
> `chatgpt_web` (Playwright) cannot run on Vercel Lambda — use API providers only.

## Common Issues

- **"LLM did not return valid JSON"** — check `ResultScore.raw_response`
- **chatgpt_web Cloudflare block** — evaluator tries real Chrome first automatically
- **Playwright not found** — `uv run playwright install chromium`
- **Gemini 429** — free tier: 15 req/min, 1500 req/day; add delay or upgrade tier
- **"Could not find result array"** — set `mapping.data_path` to the array key name
