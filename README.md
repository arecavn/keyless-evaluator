# 🔮 Oracle Search Evaluator

> LLM-as-judge search quality evaluation — score a result list against a query using ChatGPT, Gemini, or the Oracle CLI.

Inspired by the [Oracle CLI demo](https://www.youtube.com/watch?v=nsOGxKrG20M) by Peter Steinberger.

---

## What It Does

Given a **search query** and a **list of results**, the evaluator asks an LLM to judge each result on a **0–3 relevance scale**:

| Score | Label | Meaning |
|-------|-------|---------|
| **3** | ★ Highly Relevant | Perfect or near-perfect answer to the query |
| **2** | ✓ Relevant | Addresses the query but with minor gaps |
| **1** | ~ Marginal | Only tangentially related |
| **0** | ✗ Irrelevant | No meaningful connection to the query |

For each result the LLM returns:
- **Score** (0–3)
- **Reason summary** (one sentence)
- **Reason detail** (2–4 sentence elaboration)

And the overall evaluation includes:
- **nDCG** (Normalized Discounted Cumulative Gain)
- Token usage
- Full JSON export

---

## 🛑 Troubleshooting macOS Cookie Errors

If you run `oracle-eval` and get:
`ERROR: Gemini browser mode requires Chrome cookies for google.com (missing __Secure-1PSID/__Secure-1PSIDTS)`

**Why:** macOS blocks terminal apps from decrypting Chrome cookies by default for security.

**The Fix:** Use ChatGPT instead of Gemini. The ChatGPT integration natively supports `--browser-manual-login`, which physically opens a Chrome window and lets you reuse your logged-in session without needing disk-level cookie decryption:

```bash
uv run oracle-eval eval -q "hello" -f example_results.json -m gpt-4o
```

---

## Quick Start

### 1. Install

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the project
uv sync
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and add your API key(s)
```

### 3. Generate an example input

```bash
uv run oracle-eval example
```

### 4. Evaluate!

```bash
# Using OpenAI GPT-4o (needs OPENAI_API_KEY)
uv run oracle-eval eval -q "python async web framework" -f example_results.json

# Using Gemini Flash (needs GEMINI_API_KEY)
uv run oracle-eval eval -q "python async web framework" -f example_results.json \
  --provider gemini --model gemini-2.0-flash

# Using Oracle CLI in browser mode (no API key needed!)
uv run oracle-eval eval -q "python async web framework" -f example_results.json \
  --provider oracle --engine browser --model gemini-3-pro
```

---

## Providers

| Provider | Default Model | API Key Variable | Notes |
|----------|--------------|-----------------|-------|
| `openai` | `gpt-4o` | `OPENAI_API_KEY` | Also works with gpt-4o-mini, o1, gpt-5 |
| `gemini` | `gemini-2.0-flash` | `GEMINI_API_KEY` | Free API via Google AI Studio |
| `oracle` | `gpt-4o` | optional | Browser mode: no key needed; uses Chrome |

```bash
# See all providers
uv run oracle-eval providers
```

---

## 🚀 Run as a REST API (New!)

You can start `oracle-eval` as a standalone web server. It instantly provides a Fast API endpoint (`/v1/evaluate`) that automates ChatGPT on the web to evaluate results without an account.

```bash
uv run oracle-eval serve
```

Once running, simply send `POST` requests to `http://127.0.0.1:8000/v1/evaluate`.

```bash
curl -X POST "http://127.0.0.1:8000/v1/evaluate" \
     -H "Content-Type: application/json" \
     -d '{
           "query": "python async web framework",
           "results": [
             {"id": "1", "title": "FastAPI", "snippet": "A modern web framework"}
           ]
         }'
```
You get back a fully parsed JSON response with individual `0-3` scores, reason summaries, and the overall nDCG score. Interactive Swagger docs are available automatically at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

---

## Input Format

Results file must be a JSON array (or `{"results": [...]}` wrapper):

```json
[
  {
    "id": "doc_001",
    "title": "FastAPI Documentation",
    "snippet": "Modern, fast web framework for building APIs with Python",
    "url": "https://fastapi.tiangolo.com/",
    "metadata": {
      "category": "web-framework",
      "language": "Python"
    }
  },
  ...
]
```

Required fields: `id`, `title`  
Optional: `snippet`, `url`, `metadata` (any key-value pairs)

You can also pipe JSON directly:
```bash
cat my_results.json | uv run oracle-eval eval -q "my search query"
```

---

## Oracle CLI Mode (Browser, No API Key)

The [Oracle CLI](https://github.com/steipete/oracle) can drive ChatGPT or Gemini through your browser — no API key required.

```bash
# Install Oracle CLI globally
npm install -g @steipete/oracle

# Use it as the evaluator backend
uv run oracle-eval eval \
  -q "best search results for X" \
  -f results.json \
  --provider oracle \
  --engine browser \
  --model gemini-3-pro
```

Make sure you're logged into `gemini.google.com` or `chatgpt.com` in Chrome.

---

## Commands

```
oracle-eval eval       # Evaluate search results (main command)
oracle-eval detail     # Show detailed reasoning for a saved result
oracle-eval example    # Generate a sample results.json
oracle-eval providers  # List available LLM providers
```

### `eval` options

| Flag | Description |
|------|-------------|
| `-q, --query` | Search query (required) |
| `-f, --file` | JSON results file |
| `-p, --provider` | `openai` \| `gemini` \| `oracle` |
| `-m, --model` | Model name |
| `-e, --engine` | `api` \| `browser` (Oracle only) |
| `-d, --detail` | Show detailed reasoning panels |
| `-o, --output` | Save full evaluation JSON |
| `-c, --context` | Extra context about query intent |

---

## Python API

```python
import asyncio
from oracle_search_evaluator.models import EvaluationRequest, SearchResult
from oracle_search_evaluator.evaluators import get_evaluator

request = EvaluationRequest(
    query="python async web framework",
    results=[
        SearchResult(id="1", title="FastAPI", snippet="Fast Python web framework"),
        SearchResult(id="2", title="Django", snippet="The web framework for perfectionists"),
    ],
)

evaluator = get_evaluator("gemini", model="gemini-2.0-flash")
response = asyncio.run(evaluator.evaluate(request))

for score in response.scores:
    print(f"{score.score.value}/3 — {score.title}: {score.reason_summary}")

print(f"nDCG: {response.ndcg:.4f}")
```

---

## Project Structure

```
oracle_search_evaluator/
├── __init__.py
├── cli.py          # Typer CLI commands
├── models.py       # Pydantic data models (RelevanceScore, SearchResult, ...)
├── prompts.py      # LLM prompt templates
├── parser.py       # Parse LLM JSON responses
├── evaluators.py   # LLM backends (OpenAI, Gemini, Oracle CLI)
└── renderer.py     # Rich terminal output
```
