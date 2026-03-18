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
uv run main.py              # → http://127.0.0.1:8510  docs: /docs
uv run main.py --host 0.0.0.0 --port 8080   # custom bind
uv run main.py --reload                       # dev mode

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

## Docker Deployment

```bash
# Build
docker build -t keyless-evaluator:latest .

# Run (port 8511 host → 8510 container)
docker compose up -d

# Health check
curl http://localhost:8511/health
```

**Dockerfile layer cache rules** (important when editing):
- Layers 1-2: OS packages + uv — almost never invalidated
- Layer 3: pyproject.toml / uv.lock — invalidated when adding/upgrading deps
- Layer 4: `uv sync --no-install-workspace` — external wheels, cached by uv.lock
- Layer 5: `playwright install` — ~100 MB, only reruns when playwright version changes
- Layer 6: source code (`api/`, `main.py`) — **copy here last** so code edits reuse all above layers
- Layer 7: `uv sync` (local package) — fast (<1 s), runs after source copy

**Never move source COPY above playwright install** — it breaks cache for expensive layers.

### chatgpt_web on Mac — CDP Connect Mode (recommended)

WAF blocks any fresh Chrome profile launch (even real Chrome). The reliable fix is
**CDP connect mode**: open Chrome once with a debug port, log in manually, keep it running.
All evaluations reuse that live session — no WAF challenges.

```bash
# 1. Open Chrome with debug port (run once, keep window open)
open -na "Google Chrome" --args \
  --user-data-dir=/tmp/chatgpt-cdp-profile \
  --remote-debugging-port=9222

# 2. In that Chrome window: go to chatgpt.com, solve WAF once, log in

# 3. Add to .env:
CHATGPT_CDP_URL=http://localhost:9222
CHATGPT_WEB_HEADLESS=0

# 4. Restart the server — done. Chrome must stay open while server runs.
```

**Why it works**: connecting via CDP reuses the existing browser session.
WAF sees a real, already-trusted browser — no new launch, no challenge.

**Env vars**:
- `CHATGPT_CDP_URL` — if set, connects to existing Chrome via CDP (ignores all launch/profile settings)
- `CHATGPT_WEB_HEADLESS=0` — keep visible so the window stays accessible
- `CHATGPT_PROFILE_DIR` — custom profile dir (default: `~/.local/share/keyless-eval/chatgpt`)

**Browser launch fallback order** (when CDP not set):
1. `/Applications/Google Chrome.app` — real Chrome binary (most trusted by WAF)
2. `/Applications/Chromium.app`
3. `/usr/bin/google-chrome[-stable]`
4. `channel="chrome"` — Playwright's Chrome (may be "Chrome for Testing" on ARM Mac, WAF detects it)

**Launch args**: `_get_stealth_args(headless)` — minimal clean args on Mac visible mode
(removes `--disable-web-security`, `--use-gl=swiftshader`, `--disable-gpu` which look
suspicious to WAF on a machine with a real GPU and display).

### chatgpt_web / gemini_web — Google Login in Docker

Google OAuth cannot be done inside headless Docker. Workflow:

```bash
# 1. Login on Mac once (visible browser, CDP mode)
#    Follow Mac CDP setup above, then copy the profile to Docker:

# 2. Sync the saved session profile to Docker volume (via helper container)
docker run --rm \
  -v /tmp/chatgpt-cdp-profile:/src:ro \
  -v keyless-evaluator_chatgpt-profile:/dst \
  alpine sh -c "cp -rf /src/. /dst/"

# 3. Restart container
docker compose restart
```

The container uses **Xvfb** (virtual display) + `CHATGPT_WEB_HEADLESS=0` so Chromium runs
"headed" on a fake screen — avoids WAF bot-detection that targets --headless mode.

Sessions expire ~30 days. Re-run step 1-2 to refresh without rebuilding the image.

## Vercel Deployment

```bash
vercel env add GEMINI_API_KEY
vercel env add ALLOWED_ORIGINS   # comma-separated CORS origins
vercel deploy --prod
```
> `chatgpt_web` (Playwright) cannot run on Vercel Lambda — use API providers only.

## Common Issues

- **"LLM did not return valid JSON"** — `json-repair` auto-fixes unescaped quotes; check `logs/llm.log`
- **chatgpt_web WAF block** — use CDP connect mode (see above); fresh profiles always get challenged
- **chatgpt_web WAF block in Docker** — ensure Xvfb is running and `CHATGPT_WEB_HEADLESS=0`
- **Playwright not found** — `uv run playwright install chromium`
- **Gemini 429** — free tier: 15 req/min, 1500 req/day; add delay or upgrade tier
- **"Could not find result array"** — set `mapping.data_path` to the array key name
- **max_results cap** — default 20, max 500; set high (50-100) for chatgpt_web to save quota

## Commit Message Rules

- Co-author line: always use `Co-Authored-By: AI IDE`
- Never use `Co-Authored-By: Claude ...` or any model-specific attribution
