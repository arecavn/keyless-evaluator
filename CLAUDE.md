# Oracle Search Evaluator — Agent Instructions

## Project Overview

This project evaluates search result lists against queries using **LLM-as-judge** (0–3 relevance scale) via ChatGPT, Gemini, or the Oracle CLI browser mode. It is used to measure and improve search quality.

## Essential Commands

```bash
# Install / sync dependencies (always use uv — Python 3.13)
UV_PROJECT_ENVIRONMENT=/tmp/oracle-eval-venv uv sync

# Run CLI
UV_PROJECT_ENVIRONMENT=/tmp/oracle-eval-venv uv run oracle-eval --help
UV_PROJECT_ENVIRONMENT=/tmp/oracle-eval-venv uv run oracle-eval eval -q "your query" -f results.json
UV_PROJECT_ENVIRONMENT=/tmp/oracle-eval-venv uv run oracle-eval eval -q "your query" -f results.json --provider gemini
UV_PROJECT_ENVIRONMENT=/tmp/oracle-eval-venv uv run oracle-eval eval -q "your query" -f results.json --provider oracle --engine browser

# Generate example input
UV_PROJECT_ENVIRONMENT=/tmp/oracle-eval-venv uv run oracle-eval example

# Run tests
UV_PROJECT_ENVIRONMENT=/tmp/oracle-eval-venv uv run pytest tests/ -v
```

> **Note on UV_PROJECT_ENVIRONMENT**: The project's `.venv` lives in `/tmp/oracle-eval-venv` due to a macOS sandbox restriction on this machine that blocks writes inside the project dir. Export this variable once in your shell (`export UV_PROJECT_ENVIRONMENT=/tmp/oracle-eval-venv`) or add it to your shell rc.

## Architecture

```
oracle_search_evaluator/
├── cli.py          # Typer CLI (eval, detail, example, providers commands)
├── models.py       # Pydantic models: RelevanceScore (0-3), SearchResult, EvaluationRequest/Response
├── prompts.py      # LLM prompt templates (SYSTEM_PROMPT + build_user_prompt)
├── parser.py       # Parse raw LLM JSON → list[ResultScore], robust fence/noise stripping
├── evaluators.py   # LLM backends: OpenAIEvaluator, GeminiEvaluator, OracleEvaluator + factory
└── renderer.py     # Rich terminal output: tables, detail panels, nDCG stats
```

## Key Design Decisions

- **Relevance Scale**: 0 = Irrelevant, 1 = Marginal, 2 = Relevant, 3 = Highly Relevant (TREC-style)
- **Output Schema**: Each result gets `{result_id, score, reason_summary, reason_detail}` in JSON
- **Providers**: `openai` (API key), `gemini` (API key), `oracle` (browser mode = no key needed)
- **nDCG**: Auto-computed on every evaluation response
- **All async**: Evaluators are `async def evaluate(...)` — run via `asyncio.run()` in CLI

## Environment Variables (in `.env`)

```
OPENAI_API_KEY=sk-...       # for provider=openai or oracle --engine api
GEMINI_API_KEY=AI...        # for provider=gemini
ANTHROPIC_API_KEY=sk-ant-...  # for oracle with Claude models
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
4. Register it in `PROVIDER_MAP`

## Testing

```bash
uv run pytest tests/ -v
```

Tests use pre-recorded LLM responses (no live API calls by default).

## Common Issues

- **"LLM did not return valid JSON"**: The parser in `parser.py` strips markdown fences and finds the JSON array. If still failing, check the raw LLM response in `ResultScore.raw_response`.
- **Oracle browser mode**: Must be logged into `gemini.google.com` or `chatgpt.com` in Chrome before running.
- **uv cache / venv location**: `uv.toml` sets `cache-dir = "/tmp/uv-cache-oracle-eval"` to bypass a locked `.git` file in the default uv cache. The venv is placed at `/tmp/oracle-eval-venv` via `UV_PROJECT_ENVIRONMENT` because the `.venv` inside the project dir is sandbox-restricted on this machine.
