# 🔑 Keyless Evaluator

> **LLM-as-judge search quality evaluation — no extra API cost, no new account, no credit card.**
> Use the ChatGPT or Gemini account you already have. Or use Gemini's free API (1500 req/day).
> Score search results 0–3, compute nDCG, get human-readable reasons. CLI + REST API.

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![uv](https://img.shields.io/badge/managed%20by-uv-purple.svg)](https://docs.astral.sh/uv/)

---

## 🤔 What Does "Keyless" Mean?

Most LLM evaluation tools force you to create a new API key, set up billing, and pay per token — on top of what you already pay.

**Keyless Evaluator works with what you already have:**

| If you have... | Use provider | Extra cost |
|---|---|---|
| A ChatGPT Plus/Free account | `chatgpt_web` | **$0 extra** |
| A Gemini Advanced/Free account | `gemini_web` | **$0 extra** |
| A Google AI Studio key (free) | `gemini` | **$0** — 1500 req/day free |
| An OpenAI API key | `openai` | Pay-per-token |
| An Anthropic API key | `anthropic` | Pay-per-token |

The `chatgpt_web` and `gemini_web` providers drive the **web interface you already use** via a browser — the same one you open at chatgpt.com or gemini.google.com. No new billing. No new account. Your existing subscription, repurposed for evaluation.

> **True zero-cost path:** `chatgpt_web` works even without a ChatGPT account — it uses the free anonymous public interface.

---

## ✨ What It Does

Given a **search query** and a **list of results** (or any raw search API JSON), the evaluator asks an LLM to judge each result on a **0–3 relevance scale**:

| Score | Label | Meaning |
|-------|-------|---------|
| **3** | ★ Highly Relevant | Perfect or near-perfect match to the query |
| **2** | ✓ Relevant | Addresses the query, minor gaps |
| **1** | ~ Marginal | Only tangentially related |
| **0** | ✗ Irrelevant | No meaningful connection |

For every result the LLM returns a **score**, **one-sentence summary**, and **detailed justification**.
The response includes **nDCG**, token usage, provider/model used, and full JSON export.

**Use cases:**
- 📊 Measure search ranking quality objectively (job search, product search, web search, RAG retrieval)
- 🔁 CI/CD quality gate — fail pipeline if nDCG drops below threshold
- 🧪 A/B test ranking algorithms before shipping
- 🔍 Debug why bad results appear at the top
- 📋 Audit search APIs from third-party providers

---

## ⚡ Quick Start

```bash
# Install (Python 3.13 + uv required)
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/your-username/keyless-evaluator
cd keyless-evaluator
uv sync

# Generate sample input
uv run keyless-eval example

# Evaluate — free Gemini API (set GEMINI_API_KEY in .env first)
uv run keyless-eval eval -q "python async web framework" -f example_results.json

# Evaluate — anonymous ChatGPT web (zero setup, zero cost)
uv run keyless-eval eval -q "python async web framework" -f example_results.json -p chatgpt_web

# Evaluate — Gemini web (uses your Google account, no API key)
uv run keyless-eval eval -q "python async web framework" -f example_results.json -p gemini_web
```

---

## 🚀 Providers

| Provider | Model | Auth | Cost | Notes |
|---|---|---|---|---|
| `gemini` *(default)* | `gemini-2.0-flash` | `GEMINI_API_KEY` | **Free** 1500 req/day | Get key at [Google AI Studio](https://aistudio.google.com/apikey) |
| `chatgpt_web` | auto-detected | None / ChatGPT login | **$0 extra** | Anonymous or logged-in. Uses your existing ChatGPT account. |
| `gemini_web` | auto-detected | Google login | **$0 extra** | Uses your existing Google/Gemini account. |
| `openai` | `gpt-4o` | `OPENAI_API_KEY` | Pay-per-token | Direct OpenAI API |
| `anthropic` | `claude-3-5-haiku-20241022` | `ANTHROPIC_API_KEY` | Pay-per-token | Anthropic Claude API |

### Model overrides

```bash
# Gemini API — use Pro or Flash
uv run keyless-eval eval -q "..." -f results.json -p gemini -m gemini-2.5-pro-preview-03-25
uv run keyless-eval eval -q "..." -f results.json -p gemini -m gemini-2.0-flash

# Gemini Web — switch between Fast / Pro / Thinking in the UI
uv run keyless-eval eval -q "..." -f results.json -p gemini_web -m pro
uv run keyless-eval eval -q "..." -f results.json -p gemini_web -m fast
uv run keyless-eval eval -q "..." -f results.json -p gemini_web -m thinking

# OpenAI
uv run keyless-eval eval -q "..." -f results.json -p openai -m gpt-4o-mini

# Anthropic
uv run keyless-eval eval -q "..." -f results.json -p anthropic -m claude-opus-4-5
```

### Login once for web providers

```bash
# Save your ChatGPT or Gemini session (run once, then all future calls are headless)
uv run keyless-eval login -p chatgpt_web
uv run keyless-eval login -p gemini_web
```

---

## 📥 Input Formats

### Structured results (CLI / API)

```json
[
  {
    "id": "r1",
    "title": "FastAPI Documentation",
    "snippet": "Modern async web framework for Python",
    "url": "https://fastapi.tiangolo.com/",
    "metadata": {"category": "web-framework", "stars": "75k"}
  }
]
```

Required: `id`, `title`. Optional: `snippet`, `url`, `metadata`.

### Raw search API response — paste directly, zero reformatting

Works with any search API output. Auto-detects result arrays (`data`, `hits`, `results`, `items`, `docs`, `records`, `jobs`) and field names (`title`/`jobTitle`/`name`, `description`/`snippet`/`jobDescription`, etc.).

```json
{
  "input": "remote python jobs",
  "output": { ...your raw search API JSON response... },
  "mapping": {
    "title_field": "jobTitle",
    "snippet_field": "jobDescription",
    "metadata_fields": ["company", "salary", "location"]
  }
}
```

---

## 🌐 REST API

```bash
# Start server (default: http://127.0.0.1:8510)
uv run main.py
uv run main.py --host 0.0.0.0 --port 8080  # custom bind
uv run main.py --reload                      # dev mode

# Docs at http://127.0.0.1:8510/docs
```

### Evaluate endpoint

```bash
curl -X POST "http://127.0.0.1:8510/v1/evaluate?provider=gemini" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "python async web framework",
    "output": [
      {"id": "r1", "title": "FastAPI", "snippet": "Modern async Python web framework"},
      {"id": "r2", "title": "Django", "snippet": "Full-stack Python web framework"},
      {"id": "r3", "title": "Best Chili Recipe", "snippet": "Spicy chili with beans"}
    ]
  }'
```

### Advanced options

```bash
# Custom scoring prompt (replace built-in rubric with your own)
curl -X POST "http://127.0.0.1:8510/v1/evaluate?provider=gemini" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Senior Python developer Hanoi",
    "output": { ...job search API response... },
    "prompt": "Score how well this job matches the candidate. 3=perfect, 0=no match.",
    "mapping": {"title_field": "jobTitle", "snippet_field": "jobDescription"},
    "response_language": "Vietnamese",
    "batch_size": 1,
    "sleep": 3,
    "tag": "job-eval"
  }'
```

| Option | Description |
|---|---|
| `prompt` | Replace built-in TREC rubric with your own scoring criteria |
| `prompt_preset` | Use a built-in prompt preset instead of writing your own (see below) |
| `response_language` | Get reason/summary in your language (`"Vietnamese"`, `"Japanese"`, etc.) |
| `batch_size` | Evaluate N results per LLM call (use `1` for maximum accuracy on field-rich objects) |
| `sleep` | Jitter delay between batch calls in seconds — recommended for web providers to avoid bot detection |
| `tag` | Short label shown at the top of Gemini/ChatGPT messages for history identification (e.g. `"job-eval"`, `"candidate-screen"`) |
| `max_results` | Cap how many results to evaluate (default: 20) |

### Prompt presets

Built-in optimized prompts for specific domains. Use `prompt_preset` instead of writing a long `prompt`:

| Preset | Domain | Description |
|---|---|---|
| `opp_search` | Vietnamese job search | Scores job results against queries with VN language mapping, structured field priority, intern/student rules, contradiction detection |

```bash
curl -X POST "http://127.0.0.1:8510/v1/evaluate?provider=chatgpt_web" \
  -H "Content-Type: application/json" \
  -d '{"input": "tts marketing Đà Nẵng", "prompt_preset": "opp_search", "output": {...}}'
```

Priority: `prompt` (custom) > `prompt_preset` (built-in) > default SYSTEM_PROMPT.

Check available presets: `GET /health` → `prompt_presets` field.

### Sample response

```json
{
  "input": "python async web framework",
  "model": "gemini-2.0-flash",
  "provider": "gemini",
  "ndcg": 0.9243,
  "prompt_tokens": 512,
  "completion_tokens": 256,
  "scores": [
    {
      "result_id": "r1",
      "title": "FastAPI",
      "score": 3,
      "reason_summary": "FastAPI is a leading Python async web framework — perfect match.",
      "reason_detail": "The result directly addresses the query for a Python async web framework. FastAPI is built on async-first principles using ASGI and is the most popular choice for high-performance async Python APIs."
    },
    {
      "result_id": "r3",
      "title": "Best Chili Recipe",
      "score": 0,
      "reason_summary": "Completely unrelated to Python web frameworks.",
      "reason_detail": "This result is a cooking recipe with no connection to programming or web frameworks."
    }
  ]
}
```

---

## 💻 CLI Reference

```
keyless-eval eval        Evaluate search results
keyless-eval detail      Show detailed reasoning for a saved result
keyless-eval example     Generate a sample results.json to get started
keyless-eval providers   List all providers with notes
keyless-eval login       Save browser session for chatgpt_web / gemini_web
keyless-eval serve       Start the FastAPI HTTP server
```

### `eval` flags

| Flag | Short | Description |
|------|-------|-------------|
| `--input` | `-q` | Search query / evaluation criterion (required) |
| `--file` | `-f` | JSON results file (or pipe via stdin) |
| `--provider` | `-p` | `gemini` \| `chatgpt_web` \| `gemini_web` \| `openai` \| `anthropic` |
| `--model` | `-m` | Model override (e.g. `pro`, `thinking`, `gpt-4o-mini`) |
| `--tag` | `-t` | Label for web-provider chat history (e.g. `job-eval`, `screening`) |
| `--detail` | `-d` | Show detailed reasoning panels per result |
| `--output` | `-o` | Save full JSON to file |
| `--context` | `-c` | Extra context about query intent |

```bash
# Pipe results from another command
curl -s "https://your-search-api.com/search?q=remote+python+jobs" | \
  uv run keyless-eval eval -q "remote python jobs" -p gemini_web -t job-eval

# Save output and view details
uv run keyless-eval eval -q "..." -f results.json --detail --output scored.json
uv run keyless-eval detail scored.json 2   # show detail for result at index 2
```

---

## 🔧 Setup & Configuration

```bash
cp .env.example .env
```

```env
# Free — get from https://aistudio.google.com/apikey
GEMINI_API_KEY=AI...

# Optional — only needed for paid API providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# REST API — CORS origins (comma-separated, defaults to * for local dev)
ALLOWED_ORIGINS=https://your-app.com
```

---

## 🏗️ Architecture

```
api/
├── cli.py          Typer CLI — eval, detail, example, providers, login, serve
├── models.py       Pydantic models — RelevanceScore (0–3), SearchResult, EvaluationRequest/Response
├── prompts.py      LLM prompt templates — SYSTEM_PROMPT + build_user_prompt()
├── presets.py      Built-in prompt presets for specific domains (opp_search, etc.)
├── parser.py       Parse LLM JSON output — fence stripping, fallback scoring
├── evaluators.py   Backends — GeminiEvaluator, OpenAIEvaluator, ChatGPTWebEvaluator, GeminiWebEvaluator, AnthropicEvaluator
├── adapter.py      Dynamic JSON adapter — dot-path resolver, auto field detection
├── renderer.py     Rich terminal output — tables, detail panels, nDCG stats
└── server.py       FastAPI REST API — POST /v1/evaluate, GET /health

.agents/skills/
├── api-design/            REST API design standards
├── business-requirements/ Provider policy, nDCG thresholds, integration patterns
├── performance/           Async patterns, caching, cold-start optimization
└── security/              CORS, secrets, rate limiting, headers
```

---

## ☁️ Vercel Deployment

```bash
npm i -g vercel
vercel env add GEMINI_API_KEY
vercel env add ALLOWED_ORIGINS   # e.g. https://your-app.com
vercel deploy --prod
```

> **Note:** `chatgpt_web` and `gemini_web` use Playwright (browser automation) and cannot run on serverless. Use `gemini`, `openai`, or `anthropic` for Vercel deployments.

---

## 🧪 Development

```bash
uv sync                    # install deps
uv run pytest tests/ -v    # run tests
uv run main.py             # start local server at :8510
```

> **macOS note:** If `.venv` creation fails inside the project dir, set:
> `export UV_PROJECT_ENVIRONMENT=/tmp/keyless-eval-venv`

---

## 🔍 How the Web Providers Work

`chatgpt_web` and `gemini_web` use [Playwright](https://playwright.dev/) to control a real Chrome browser window — the same way you'd use ChatGPT or Gemini manually. No scraping, no unofficial API.

- **Anonymous mode** (`chatgpt_web`): opens a temporary Chrome session, no login needed
- **Logged-in mode**: run `keyless-eval login` once to save your session, then all future calls run headless automatically
- **Bot detection**: the evaluator uses real Chrome with stealth patches, randomized delays, and human-like jitter between requests

### CDP mode — recommended for servers

CDP (Chrome DevTools Protocol) connects to an already-running Chrome instead of launching a new one. This is the most reliable setup for `chatgpt_web` — WAF sees a real, already-trusted browser.

**macOS:**

```bash
# 1. Open Chrome with debug port (keep this window open)
open -na "Google Chrome" --args --user-data-dir=/tmp/chatgpt-cdp-profile --remote-debugging-port=9222

# 2. In that Chrome window: go to chatgpt.com, log in, solve any WAF challenge

# 3. Add to .env:
CHATGPT_CDP_URL=http://127.0.0.1:9222

# 4. Start server
uv run main.py
```

**Headless Ubuntu server:**

```bash
# 1. Install Chrome
wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y /tmp/chrome.deb

# 2. Start headless Chrome with CDP
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chatgpt-cdp-profile --no-first-run --no-default-browser-check --no-sandbox --disable-gpu --headless=new &

# 3. Verify
curl http://127.0.0.1:9222/json/version

# 4. Copy a logged-in profile from your Mac (headless can't log in interactively)
# On Mac: rsync -a /tmp/chatgpt-cdp-profile/ user@server:/tmp/chatgpt-cdp-profile/

# 5. Add to .env and start
echo "CHATGPT_CDP_URL=http://127.0.0.1:9222" >> .env
uv run main.py
```

**Ubuntu with desktop** (can log in directly):

```bash
google-chrome --user-data-dir=/tmp/chatgpt-cdp-profile --remote-debugging-port=9222 --no-first-run --no-default-browser-check
# Log in to ChatGPT in that window, then keep it open
```

---

## 📄 License

MIT — free to use, modify, and distribute.

---

<!-- GitHub Topics (add these in your repo Settings → Topics):
llm-as-judge, search-evaluation, ndcg, information-retrieval, search-quality,
relevance-scoring, rag-evaluation, retrieval-evaluation, llm, gemini, chatgpt,
openai, anthropic, fastapi, playwright, search-ranking, python, cli, rest-api
-->
