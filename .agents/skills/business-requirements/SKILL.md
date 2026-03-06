---
name: business-requirements
description: >
  Business requirements, SLAs, cost constraints, integration patterns, and quality standards
  for the keyless-evaluator search quality measurement service. Use this skill when making
  product decisions, designing new features, estimating costs, or integrating this service
  into a search pipeline. Covers allowed providers, cost limits, evaluation SLAs, and
  the nDCG quality metric interpretation.
---

# Business Requirements — Keyless Evaluator

## What This Service Does

**Purpose**: Measure and improve search result quality using LLM-as-judge scoring.

**Core value**: Give every search result a 0–3 relevance score with human-readable reasons,
so we can compute nDCG and compare ranking algorithm changes objectively — without hiring
human annotators or paying per-token API fees for every evaluation.

---

## Provider Selection Policy

### Priority order

1. **`gemini`** — primary for automated pipelines
   - Free: 1500 req/day, 15 req/min (Google AI Studio key)
   - No per-request cost at free tier → use for bulk evaluation jobs
   - Go-to choice for CI/CD search quality gates

2. **`chatgpt_web`** — primary for local / ad-hoc evaluation
   - No key required, no account needed
   - Use for one-off evaluations during development
   - NOT suitable for production server (browser cannot run in Lambda)

3. **`anthropic`** — high-quality reasoning, use when score accuracy matters most
   - Costs money — use sparingly (spot-checks, final evaluation runs)
   - Best for nuanced queries where reasoning quality is critical

4. **`openai`** — fallback when Gemini is unavailable
   - Costs money — do not use for bulk jobs without a budget cap

### When to switch providers

| Situation | Action |
|---|---|
| Gemini 429 rate limit hit | Queue with exponential backoff; do not switch to OpenAI automatically |
| Gemini API down | Fall back to `chatgpt_web` for local, `anthropic` for server |
| Evaluation results are reviewed by humans | Use `anthropic` for highest reasoning quality |
| Batch job > 1500 results/day | Split across days or upgrade Gemini tier |

---

## Cost Constraints

| Provider | Unit cost | Budget rule |
|---|---|---|
| Gemini (free tier) | $0 | Preferred for all automated use |
| Gemini (paid) | ~$0.075 per 1M tokens | Only if free tier is exhausted for business-critical jobs |
| Anthropic Haiku | ~$0.25 per 1M input tokens | Max 200 evaluations/month without explicit approval |
| OpenAI gpt-4o | ~$2.50 per 1M input tokens | Do not use for bulk — spot-checks only |

**Alert threshold**: If monthly LLM spend exceeds $10, review usage and optimize.

---

## Evaluation Quality Standards

### Scoring scale interpretation

| Score | Label | Action |
|---|---|---|
| 3 | Highly Relevant | Perfect match — boost in ranking experiments |
| 2 | Relevant | Good result — acceptable in top 5 |
| 1 | Marginal | Borderline — investigate if appearing in top 3 |
| 0 | Irrelevant | Bad result — investigate root cause immediately |

### nDCG thresholds

| nDCG@5 | Quality | Action |
|---|---|---|
| ≥ 0.90 | Excellent | No action needed |
| 0.75 – 0.89 | Good | Monitor; improve if trending down |
| 0.60 – 0.74 | Acceptable | Investigate top-ranking irrelevant results |
| < 0.60 | Poor | Escalate; block ranking model change if in CI |

---

## Integration Patterns

### Pattern 1: CI/CD search quality gate

Run after each search algorithm change. Fail the pipeline if nDCG drops below threshold.

```bash
# In CI pipeline
uv run keyless-eval eval \
  -q "$(cat test_query.txt)" \
  -f test_results.json \
  -p gemini \
  --output ci_scored.json

python3 -c "
import json, sys
data = json.load(open('ci_scored.json'))
ndcg = data.get('ndcg', 0)
print(f'nDCG: {ndcg:.4f}')
sys.exit(0 if ndcg >= 0.75 else 1)
"
```

### Pattern 2: REST API integration (from search service)

Other services call the evaluator over HTTP. Always include timeout and retry logic:

```python
import httpx

async def evaluate_results(query: str, results: list[dict]) -> dict:
    async with httpx.AsyncClient(timeout=45.0) as client:
        for attempt in range(3):
            try:
                resp = await client.post(
                    "http://localhost:8000/v1/evaluate",
                    params={"provider": "gemini"},
                    json={"query": query, "results": results},
                )
                resp.raise_for_status()
                return resp.json()
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
```

### Pattern 3: Batch evaluation (offline)

For evaluating large result sets (>100 items) or multiple queries:

```bash
# Loop over queries
for query_file in queries/*.txt; do
  query=$(cat "$query_file")
  base=$(basename "$query_file" .txt)
  uv run keyless-eval eval \
    -q "$query" \
    -f "results/${base}.json" \
    --output "scored/${base}.json"
  sleep 4   # respect 15 req/min Gemini limit
done
```

---

## Data Handling Requirements

### PII / Privacy

- Search queries and result titles MAY contain user-generated or sensitive content
- **Do not log** query text or result content — log only metadata (provider, num_results, latency)
- **Do not store** evaluation results outside the explicitly requested `--output` file
- LLM responses sent to external providers (Gemini, OpenAI, Anthropic) — ensure data processing agreements are in place if queries contain personal data

### Data retention

| Data | Retention |
|---|---|
| `--output` scored JSON files | Keep as long as needed for comparison |
| Server access logs | 30 days max; strip query params if they contain query text |
| LLM API usage logs | Provider handles; governed by their data policy |

---

## SLA & Reliability

| Requirement | Value |
|---|---|
| Availability (server mode) | 99.5% (Vercel SLA covers infra) |
| Evaluation latency P95 | < 15s (Gemini), < 60s (ChatGPT Web) |
| Error rate | < 1% of requests return 5xx |
| LLM parse failure rate | < 0.5% (parser is robust to markdown fences) |

### Degradation strategy

If the primary provider (Gemini) is unavailable:
1. Return `503` immediately with `Retry-After: 60` header
2. Do NOT silently fall back to a more expensive provider without operator consent
3. The caller is responsible for fallback logic

---

## Interpretation Guide for Non-Technical Stakeholders

- **nDCG = 1.0**: Perfect ranking — every relevant result is at the top
- **nDCG = 0.5**: Poor ranking — relevant results are scattered or buried
- **Average score ≥ 2.0/3.0** for top-5 results: acceptable product quality
- **Any result with score 0 in position 1–3**: critical ranking bug — investigate

---

## Future Requirements (Planned)

- [ ] Multi-query batch endpoint: `POST /v1/evaluate/batch`
- [ ] Async job submission + polling: `POST /v1/jobs`, `GET /v1/jobs/{id}`
- [ ] nDCG trend charts via `/v1/history` (requires persistent storage)
- [ ] Per-API-key usage tracking (for multi-tenant SaaS)
- [ ] Support for custom relevance scales (binary, 5-point)
