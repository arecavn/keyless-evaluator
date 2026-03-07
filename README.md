# 🔑 Keyless Evaluator

> **High-quality LLM-as-judge search evaluation — no API key, no account, no credit card.**  
> Uses ChatGPT's latest model through its public web interface, completely anonymously.  
> Or plug in Gemini (free 1500 req/day), OpenAI, or Anthropic. Your choice.

---

## ✨ Why This Is Different

Most search evaluation tools require an API key, a paid plan, or both.  
**Keyless Evaluator doesn't.**

| | Keyless Evaluator | Traditional tools |
|---|---|---|
| **No API key needed** | ✅ `chatgpt_web` mode | ❌ Always required |
| **No account needed** | ✅ Fully anonymous | ❌ Always required |
| **Latest ChatGPT model** | ✅ Auto, always up to date | ❌ Pinned to your tier |
| **Free Gemini quota** | ✅ 1500 req/day | — |
| **Paste any search API response** | ✅ `/v1/evaluate/raw` auto-adapts | ❌ Must reformat |
| **REST API + CLI** | ✅ Both included | Varies |
| **nDCG scoring** | ✅ Auto-computed | Varies |

```bash
# No account. No key. Full ChatGPT quality. Works right now.
uv run keyless-eval eval -q "remote jobs" -f results.json -p chatgpt_web
```

> 🔒 **How?** The `chatgpt_web` provider drives ChatGPT's anonymous public web session via
> Playwright — the same interface anyone can use at [chatgpt.com](https://chatgpt.com) without
> logging in. ChatGPT serves its latest available model to anonymous users automatically.

---

## What It Does

Given a **search query** and a **list of results** (or any raw search API JSON response), the evaluator asks an LLM to judge each result on a **0–3 relevance scale**:

| Score | Label | Meaning |
|-------|-------|---------|
| **3** | ★ Highly Relevant | Perfect or near-perfect answer to the query |
| **2** | ✓ Relevant | Addresses the query but with minor gaps |
| **1** | ~ Marginal | Only tangentially related |
| **0** | ✗ Irrelevant | No meaningful connection to the query |

For each result the LLM returns a **score**, **reason summary**, and **reason detail**.  
The overall response includes **nDCG**, token usage, and a full JSON export.

---

## Quick Start

### 1. Install

```bash
# Requires Python 3.13 + uv
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### 2. Configure (optional — only needed for API providers)

```bash
cp .env.example .env
# Add GEMINI_API_KEY from https://aistudio.google.com/apikey (free, 1500 req/day)
```

### 3. Generate an example input

```bash
uv run keyless-eval example
```

### 4. Evaluate

```bash
# Default: Gemini API (free quota — set GEMINI_API_KEY in .env)
uv run keyless-eval eval -q "python async web framework" -f example_results.json

# No account / no key — anonymous ChatGPT web (opens Chrome window)
uv run keyless-eval eval -q "python async web framework" -f example_results.json -p chatgpt_web

# Anthropic Claude
uv run keyless-eval eval -q "python async web framework" -f example_results.json -p anthropic

# With detail panels + save output
uv run keyless-eval eval -q "python async web framework" -f example_results.json --detail --output scored.json
```

---

## Providers

| Provider | Default Model | Env Variable | Notes |
|---|---|---|---|
| `gemini` *(default)* | `gemini-2.0-flash` | `GEMINI_API_KEY` | **Free** 1500 req/day via [Google AI Studio](https://aistudio.google.com/apikey) |
| `chatgpt_web` | `auto` | None | Anonymous ChatGPT web via Playwright. No account or key needed. |
| `openai` | `gpt-4o` | `OPENAI_API_KEY` | Direct OpenAI API |
| `anthropic` | `claude-3-5-haiku-20241022` | `ANTHROPIC_API_KEY` | Anthropic Claude API |

```bash
uv run keyless-eval providers   # see full table with notes
```

---

## REST API

Start the HTTP server and integrate with any service:

```bash
uv run main.py
# → http://127.0.0.1:8000
# → Docs: http://127.0.0.1:8000/docs

uv run main.py --host 0.0.0.0 --port 8080   # custom bind
uv run main.py --reload                       # dev mode
```

### Standard input (structured results list)

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/evaluate?provider=gemini" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "python async web framework",
    "results": [
      {"id": "r1", "title": "FastAPI", "snippet": "Modern async web framework for Python"},
      {"id": "r2", "title": "Django", "snippet": "Full-stack web framework for Python"},
      {"id": "r3", "title": "Best Chili Recipe", "snippet": "Spicy chili with beans"}
    ]
  }' | python3 -m json.tool
```

### Dynamic raw input — paste any search API response directly

No reformatting needed. Paste your search API's JSON output verbatim:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/evaluate/raw?provider=gemini" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "remote jobs",
    "raw": { ...your full search API JSON response... }
  }' | python3 -m json.tool
```

The adapter **auto-detects** the results array (`data`, `results`, `hits`, etc.) and common field names (`id`, `jobTitle`/`title`/`name`, `jobDescription`/`description`/`snippet`).

**With explicit mapping** (for non-standard field names):

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/evaluate/raw?provider=gemini" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "remote jobs",
    "max_results": 10,
    "raw": { ...your search API JSON... },
    "mapping": {
      "data_path": "data",
      "id_field": "id",
      "title_field": "jobTitle",
      "snippet_field": "jobDescription",
      "metadata_fields": ["company", "salary", "location", "employmentTypeEn"]
    }
  }' | python3 -m json.tool
```

### Health check

```bash
curl http://127.0.0.1:8000/health
```

---

## Input Format

### Standard (`/v1/evaluate`)

JSON array of results, or `{"results": [...]}` wrapper:

```json
[
  {
    "id": "doc_001",
    "title": "FastAPI Documentation",
    "snippet": "Modern, fast web framework for building APIs with Python",
    "url": "https://fastapi.tiangolo.com/",
    "metadata": {"category": "web-framework", "language": "Python"}
  }
]
```

Required: `id`, `title`. Optional: `snippet`, `url`, `metadata`.

Pipe from stdin:
```bash
cat my_results.json | uv run keyless-eval eval -q "my search query"
```

### Dynamic raw (`/v1/evaluate/raw`)

Any JSON body your search API returns. Supported auto-detected array keys: `data`, `results`, `hits`, `items`, `docs`, `records`, `jobs`.

---

## Python API

```python
import asyncio
from keyless_evaluator.models import EvaluationRequest, SearchResult
from keyless_evaluator.evaluators import get_evaluator

request = EvaluationRequest(
    query="python async web framework",
    results=[
        SearchResult(id="1", title="FastAPI", snippet="Fast Python web framework"),
        SearchResult(id="2", title="Django", snippet="The web framework for perfectionists"),
    ],
)

evaluator = get_evaluator("gemini")  # or "openai", "anthropic", "chatgpt_web"
response = asyncio.run(evaluator.evaluate(request))

for score in response.scores:
    print(f"{score.score.value}/3 — {score.title}: {score.reason_summary}")

print(f"nDCG: {response.ndcg:.4f}")
```

---

## CLI Commands

```
keyless-eval eval        Evaluate search results (main command)
keyless-eval detail      Show detailed reasoning for a saved result
keyless-eval example     Generate a sample results.json
keyless-eval providers   List available LLM providers
keyless-eval serve       Start the FastAPI HTTP server (or: uv run main.py)
```

### `eval` options

| Flag | Description |
|------|-------------|
| `-q, --query` | Search query (required) |
| `-f, --file` | JSON results file (or pipe via stdin) |
| `-p, --provider` | `gemini` \| `chatgpt_web` \| `openai` \| `anthropic` |
| `-m, --model` | Model name override |
| `-d, --detail` | Show detailed reasoning panels |
| `-o, --output` | Save full evaluation JSON |
| `-c, --context` | Extra context about query intent |

---

## Project Structure

```
keyless_evaluator/
├── cli.py          # Typer CLI (eval, detail, example, providers, serve)
├── models.py       # Pydantic models (RelevanceScore, SearchResult, EvaluationRequest/Response, RawEvaluationRequest)
├── prompts.py      # LLM prompt templates
├── parser.py       # Parse LLM JSON responses (fence-stripping, fallback scoring)
├── evaluators.py   # LLM backends: Gemini, OpenAI, Anthropic, ChatGPTWeb + factory
├── adapter.py      # Dynamic raw JSON input adapter (dot-path resolver, auto field detection)
├── renderer.py     # Rich terminal output: tables, detail panels, nDCG stats
└── server.py       # FastAPI app: POST /v1/evaluate, POST /v1/evaluate/raw, GET /health

.agents/skills/
├── api-design/SKILL.md          # API input/output standards, validation, error format
├── security/SKILL.md            # Security: secrets, CORS, headers, rate limiting
├── performance/SKILL.md         # Async patterns, concurrency, caching, cold-start
└── business-requirements/SKILL.md  # Provider policy, cost, nDCG thresholds, SLA

vercel.json          # Vercel serverless deployment
requirements.txt     # Pip-compatible deps for Vercel Python runtime
```

---

## Vercel Deployment

```bash
npm i -g vercel
vercel env add GEMINI_API_KEY
vercel env add ALLOWED_ORIGINS   # e.g. https://your-app.com
vercel deploy --prod
```

> **Note**: The `chatgpt_web` provider uses Playwright and cannot run on Vercel Lambda. Use `gemini`, `openai`, or `anthropic` in serverless deployments.

---

## Development

```bash
uv sync                          # install deps
uv run pytest tests/ -v         # run tests
uv run main.py                   # start local server
```

> **macOS note**: If `.venv` creation fails (sandbox restriction), set:
> `export UV_PROJECT_ENVIRONMENT=/tmp/keyless-eval-venv`
