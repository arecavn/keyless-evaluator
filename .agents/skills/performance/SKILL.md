---
name: api-performance-throughput
description: >
  Performance and throughput standards for the keyless-evaluator API: async patterns,
  concurrency, timeout management, caching, connection pooling, and Vercel cold-start
  optimization. Use this skill when the API is slow, hitting timeouts, or needs to handle
  higher request volumes. Also applies when adding new evaluator providers.
---

# API Performance & Throughput Standards

## Baseline Targets

| Metric | Target | Notes |
|---|---|---|
| P50 latency (Gemini) | < 3s | Single evaluation, 3–5 results |
| P99 latency (Gemini) | < 10s | |
| P50 latency (ChatGPT Web) | < 30s | Browser startup adds overhead |
| Throughput (Gemini) | 15 req/min | Free tier limit |
| Cold start (Vercel) | < 2s | Python import budget |
| Health check | < 100ms | No LLM calls |

---

## Async Patterns — Critical Rules

All evaluators are `async` and MUST follow these rules:

### 1. Never block the event loop

```python
# ✅ Correct — run sync SDK in executor
loop = asyncio.get_event_loop()
response = await loop.run_in_executor(None, lambda: model.generate_content(...))

# ❌ Wrong — blocks the entire server
response = model.generate_content(...)
```

Sync SDKs that block: `google-generativeai` (use executor).
Async SDKs: `openai` (AsyncOpenAI), `anthropic` (AsyncAnthropic) — use `await` directly.

### 2. Propagate timeouts everywhere

```python
# ✅ Always set explicit timeouts
async with asyncio.timeout(60):
    response = await evaluator.evaluate(request)

# ❌ Never leave a coroutine without a timeout — it can hang forever
response = await evaluator.evaluate(request)
```

Recommended timeouts by provider:
| Provider | Connect timeout | Total timeout |
|---|---|---|
| Gemini | 10s | 30s |
| OpenAI | 10s | 30s |
| Anthropic | 10s | 30s |
| ChatGPT Web | 15s | 120s |

### 3. Use `asyncio.gather` for parallel work (future)

If evaluating with multiple providers simultaneously:
```python
results = await asyncio.gather(
    gemini_evaluator.evaluate(request),
    anthropic_evaluator.evaluate(request),
    return_exceptions=True,
)
```

---

## Concurrency Management

### Current architecture (single-provider, single-request)

The server handles concurrent HTTP requests via uvicorn's default thread pool. Each `/v1/evaluate` call runs one LLM request. This is correct for the current use case.

### Scaling patterns (when needed)

**Horizontal**: Deploy multiple instances behind a load balancer / Vercel Edge  
**Worker pool**: For batch evaluation, use `asyncio.Semaphore` to cap concurrent LLM calls:

```python
_sem = asyncio.Semaphore(5)  # max 5 concurrent LLM calls

async def evaluate_with_limit(evaluator, request):
    async with _sem:
        return await evaluator.evaluate(request)
```

---

## Caching Strategy

### What to cache

| Data | Cache type | TTL |
|---|---|---|
| Identical `(query, results, provider, model)` evaluations | In-memory LRU or Redis | 1 hour |
| Playwright browser context | Process-level singleton | Per process lifetime |
| `playwright install` check | Skip if browser binary exists | Startup only |

### Cache key

```python
import hashlib, json

def cache_key(request: EvaluationRequest, provider: str, model: str) -> str:
    payload = {"q": request.query, "r": [r.id for r in request.results], "p": provider, "m": model}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
```

### What NOT to cache

- `chatgpt_web` responses — browser session state is non-deterministic
- Responses with `raw_response` exposed (may contain ephemeral tokens)

---

## Cold Start Optimization (Vercel / Serverless)

Python import time is the primary cold-start cost. Keep top-level imports minimal.

### Current pattern (correct)

Heavy SDKs are imported **inside** the evaluator's `evaluate()` method, not at module top level:

```python
# ✅ Lazy import — only loads when this provider is actually called
async def evaluate(self, request):
    from openai import AsyncOpenAI   # ← inside the method
    ...
```

This means:
- `keyless_evaluator.server` imports in < 200ms
- Gemini SDK only loads when the first Gemini request arrives (~800ms penalty once)
- If only Gemini is used, OpenAI/Anthropic SDKs are **never loaded**

### Keep imports clean at module level

Only import stdlib and core project modules at the top of each file. Never import `openai`, `anthropic`, or `google.generativeai` at module level.

---

## HTTP Connection Efficiency

### HTTPX (used by underlying SDKs)

Ensure connection pooling is enabled (default in `httpx.AsyncClient`). Do not create a new client per request:

```python
# ✅ Reuse client across requests (module-level or lifespan-managed)
_client = httpx.AsyncClient(http2=True, timeout=30.0)

# ❌ Creates a new TCP connection per request
async with httpx.AsyncClient() as client:  # inside the handler
    ...
```

For FastAPI, the best place is `lifespan` context manager in `server.py`.

---

## Vercel Function Configuration

In `vercel.json`:
- `maxDuration: 60` — needed for LLM calls (free plan max is 60s)
- `memory: 512` — minimum for Playwright; use 1024MB if chatgpt_web is primary
- `chatgpt_web` on Vercel is **not recommended** — browsers cannot run in serverless Lambdas

```json
"functions": {
  "keyless_evaluator/server.py": {
    "maxDuration": 60,
    "memory": 512
  }
}
```

---

## Monitoring & Observability

### Response timing — add to every evaluation response (optional header)

```python
import time

@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)
    response.headers["X-Response-Time-Ms"] = str(duration_ms)
    return response
```

### What to log per request

- Provider + model used
- Number of results evaluated
- Total latency (ms)
- Token counts (prompt + completion) when available
- Error type (not message) on failure

### What NOT to log

- Query text (may contain PII)
- Result snippets or titles (may contain PII)
- API keys or tokens
