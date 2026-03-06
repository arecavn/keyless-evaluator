# Keyless Evaluator — Agent Instructions

## Project Overview

This project evaluates search result lists against queries using **LLM-as-judge** (0–3 relevance scale) via Gemini, OpenAI, Anthropic, or anonymous ChatGPT Web. No custom browser wrapper needed for API providers.

## Essential Commands

```bash
# Install / sync dependencies (always use uv — Python 3.13)
uv sync

# Run CLI
uv run keyless-eval --help
uv run keyless-eval eval -q "your query" -f results.json
uv run keyless-eval eval -q "your query" -f results.json -p chatgpt_web
uv run keyless-eval eval -q "your query" -f results.json -p anthropic
uv run keyless-eval providers

# Generate example input
uv run keyless-eval example

# Start HTTP API server
uv run keyless-eval serve

# Run tests
uv run pytest tests/ -v
```

> **macOS note**: `uv.toml` sets `cache-dir = "/tmp/uv-cache-keyless-eval"` to bypass a locked `.git` file in the default uv cache. Export `UV_PROJECT_ENVIRONMENT=/tmp/keyless-eval-venv` if `.venv` creation fails inside the project dir.

## Architecture

```
keyless_evaluator/
├── cli.py          # Typer CLI (eval, detail, example, providers, serve)
├── models.py       # Pydantic models: RelevanceScore (0-3), SearchResult, EvaluationRequest/Response
├── prompts.py      # LLM prompt templates (SYSTEM_PROMPT + build_user_prompt)
├── parser.py       # Parse raw LLM JSON → list[ResultScore], robust fence/noise stripping
├── evaluators.py   # Backends: OpenAIEvaluator, GeminiEvaluator, ChatGPTWebEvaluator, AnthropicEvaluator + factory
├── renderer.py     # Rich terminal output: tables, detail panels, nDCG stats
└── server.py       # FastAPI REST API (POST /v1/evaluate, GET /health)
```

Deployment:
```
vercel.json         # Vercel serverless deployment config
requirements.txt    # Pinned deps for Vercel Python runtime
```

Agent skill files (for Claude Code / Antigravity):
```
.claude/skills/keyless-evaluator/SKILL.md
```

## Key Design Decisions

- **Relevance Scale**: 0 = Irrelevant, 1 = Marginal, 2 = Relevant, 3 = Highly Relevant (TREC-style)
- **Output Schema**: Each result gets `{result_id, score, reason_summary, reason_detail}` in JSON
- **Providers**: `gemini` (default, free API key), `chatgpt_web` (no key), `openai`, `anthropic`
- **Fast**: No complex Node.js/npx dependencies
- **nDCG**: Auto-computed on every evaluation response
- **All async**: Evaluators are `async def evaluate(...)` — run via `asyncio.run()` in CLI
- **CORS**: Configurable via `ALLOWED_ORIGINS` env var for production deployments

## Environment Variables (in `.env`)

```
GEMINI_API_KEY=AI...       # for provider=gemini (free from aistudio.google.com)
OPENAI_API_KEY=sk-...      # for provider=openai
ANTHROPIC_API_KEY=sk-ant-... # for provider=anthropic
ALLOWED_ORIGINS=https://your-domain.com  # CORS, comma-separated
```

## Input Format

JSON array of results (or `{"results": [...]}` wrapper):
```json
[
  {"id": "doc1", "title": "...", "snippet": "...", "url": "...", "metadata": {...}},
  ...
]
```
Required: `id`, `title`. Optional: `snippet`, `url`, `metadata`.

## Adding a New Provider

1. Create a class in `evaluators.py` that extends `BaseEvaluator`
2. Implement `async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse`
3. Use `self._build_response(request, scores)` to wrap results
4. Register it in `PROVIDER_MAP` and `_DEFAULT_MODELS`

## Vercel Deployment

```bash
npm i -g vercel          # install Vercel CLI once
vercel env add GEMINI_API_KEY
vercel env add ALLOWED_ORIGINS
vercel deploy --prod
```

Secrets are referenced as `@secret-name` in `vercel.json`.

## Testing

```bash
uv run pytest tests/ -v
```

Tests use pre-recorded LLM responses (no live API calls by default).

## Common Issues

- **"LLM did not return valid JSON"**: The parser in `parser.py` strips markdown fences and finds the JSON array. If still failing, check the raw LLM response in `ResultScore.raw_response`.
- **chatgpt_web Cloudflare block**: Use real Chrome (`channel="chrome"`), not headless Chromium. The evaluator tries Chrome first automatically.
- **Playwright not found**: Run `uv run playwright install chromium` once.
- **uv cache / venv location**: `uv.toml` sets `cache-dir = "/tmp/uv-cache-keyless-eval"`. Use `UV_PROJECT_ENVIRONMENT=/tmp/keyless-eval-venv` if `.venv` inside the project is sandbox-restricted.
