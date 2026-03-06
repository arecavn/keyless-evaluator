---
name: api-security
description: >
  Security standards for this REST API: authentication, authorization, input sanitization,
  CORS, secrets management, rate limiting, and secure deployment. Use this skill when adding
  new endpoints, changing auth, reviewing CORS config, or deploying to production environments.
  Applies to all FastAPI server code and Vercel deployment configuration.
---

# API Security Standards

## Secrets Management

### Rules — never violate

- **Never** hardcode API keys, tokens, or passwords in source code
- **Never** commit `.env` files — only commit `.env.example` with placeholder values
- **Never** log or echo secrets in error messages, health checks, or debug output
- **Never** return provider API keys in any API response

### Where secrets live

| Environment | Secret Store |
|---|---|
| Local dev | `.env` file (gitignored) |
| Vercel production | Vercel Encrypted Env (`vercel env add KEY`) |
| CI/CD | GitHub Actions Secrets or equivalent |

### In code

```python
# ✅ Correct
api_key = os.environ.get("GEMINI_API_KEY", "")

# ❌ Never
api_key = "AIza..."
```

The `/health` endpoint MUST only report whether a key is set (`bool`), never its value:
```python
{"gemini_key_set": bool(os.environ.get("GEMINI_API_KEY"))}
```

---

## CORS Policy

Configure via `ALLOWED_ORIGINS` environment variable (comma-separated).

```python
# Production: explicit allowlist
ALLOWED_ORIGINS=https://your-app.com,https://admin.your-app.com

# Development only: wildcard (never in production)
ALLOWED_ORIGINS=*
```

Rules:
- Wildcard `*` is only acceptable for fully public, unauthenticated, read-only APIs
- The `/v1/evaluate` endpoint accepts POST — always restrict origins in production
- `allow_credentials=True` requires explicit origins (not `*`)

---

## Security Headers

All responses MUST include these headers (configured in `vercel.json` and middleware):

| Header | Value | Purpose |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | Prevent MIME sniffing |
| `X-Frame-Options` | `DENY` | Prevent clickjacking |
| `X-XSS-Protection` | `1; mode=block` | Legacy XSS filter |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limit referrer leakage |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | Disable unused browser APIs |
| `Cache-Control` | `no-store` on `/v1/*` | Prevent caching of evaluation results |

---

## Input Validation & Sanitization

All input is validated by Pydantic models before reaching business logic.

### Key rules

1. **Max lengths** — enforce on every string field (see api-design skill)
2. **Array bounds** — max 100 results per request
3. **Type coercion** — Pydantic handles this; never cast unvalidated user input manually
4. **HTML/script injection** — the LLM prompt builder (`prompts.py`) MUST treat all user input as data, never as markup or code
5. **Metadata keys** — strip or reject keys containing unusual characters (`<`, `>`, `"`, `\n`)

### In `prompts.py` — safe interpolation pattern

```python
# ✅ Safe — data inserted as plain text in markdown, not as code
lines.append(f"- **Title**: {result.title}")

# ❌ Unsafe — never eval, exec, or format as code
eval(result.title)
```

---

## Rate Limiting

### LLM provider limits (upstream)

| Provider | Free limit | Hard limit |
|---|---|---|
| Gemini Flash | 15 req/min, 1500 req/day | Yes — 429 returned |
| OpenAI | Depends on tier | Yes |
| Anthropic | Depends on tier | Yes |
| ChatGPT Web | ~20 req/hour (estimated) | Soft — may block |

### Application-level rate limiting (recommended for production)

Add to `server.py` or via reverse proxy / Vercel Edge Middleware:

```python
# Recommended: use slowapi (ASGI rate limiter)
# uv add slowapi

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/v1/evaluate")
@limiter.limit("10/minute")   # per IP
async def evaluate_results(request: Request, ...):
    ...
```

For Vercel, also set `X-RateLimit-*` headers for client transparency.

---

## Authentication (optional, for multi-tenant use)

If this API is exposed publicly with per-client billing or access control, add Bearer token auth:

```python
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Security

security = HTTPBearer(auto_error=False)

async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    if not credentials or credentials.credentials != os.environ.get("API_TOKEN"):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
```

Do NOT implement auth if the API is internal-only behind a VPN or private network.

---

## Error Handling — Security Constraints

- **Never** return Python stack traces in production (set `debug=False`)
- **Never** reveal internal file paths, library names, or model names beyond what's in the contract
- Log exceptions server-side with full context; return only a generic `"detail"` to the client
- Return `503` (not `500`) when LLM provider is unavailable — prevents probing internal architecture

```python
# ✅ Safe error response
raise HTTPException(status_code=503, detail="Evaluation service temporarily unavailable.")

# ❌ Leaks internal info
raise HTTPException(status_code=500, detail=str(exc))  # may contain key fragments, paths
```

---

## Dependency Security

- Keep dependencies pinned in `uv.lock` (committed to git)
- Run `uv sync --upgrade` periodically and review changelogs
- Never add a dependency that executes remote code on import
- Playwright installs browser binaries — ensure the install step runs in a trusted environment
