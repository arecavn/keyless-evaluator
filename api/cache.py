"""
Evaluation result cache for keyless-evaluator.

Key format:  keyless_eval:{prefix}:{query_slug}:{fp8}

  prefix     = body.tag if set (e.g. "opp-search"), else provider slug
  query_slug = first 40 chars of input, lowercased, non-alnum → hyphen
  fp8        = first 8 chars of SHA256(input + sorted_result_ids + prompt
               + provider + model + response_language + system_prompt_ver)

Examples:
  keyless_eval:opp-search:senior-python-backend-hanoi:a3f4b2c1
  keyless_eval:gemini:remote-python-jobs:d4e5f6a7
  keyless_eval:chatgpt-web:intern-hcm:c7d8e9f0

Search (Redis):  KEYS keyless_eval:opp-search:*intern*
Search (files):  ls cache/ | grep "opp-search.*intern"

Backends (auto-selected at startup):
  REDIS_URL set  → Redis (supports cross-process sharing, KEYS pattern search)
  default        → File cache in CACHE_DIR (default: ./cache/)

Env vars:
  CACHE_TTL   seconds before a cached result expires (default: 157680000 = 5 years)
              set to 0 to disable caching entirely
  CACHE_DIR   directory for file-based cache (default: ./cache/)
  REDIS_URL   Redis connection URL (e.g. redis://localhost:6379/0)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time

from prompts import SYSTEM_PROMPT as _SYSTEM_PROMPT, PROMPT_BUILDER_VER as _PROMPT_BUILDER_VER
from presets import PRESETS as _PRESETS
_SYSTEM_PROMPT_VER = hashlib.sha256(_SYSTEM_PROMPT.encode()).hexdigest()[:8]
_PRESETS_VER = hashlib.sha256(json.dumps(_PRESETS, sort_keys=True).encode()).hexdigest()[:8]

from models import EvaluationRequest, EvaluationResponse

_log = logging.getLogger("keyless_evaluator.cache")

_CACHE_TTL = int(os.environ.get("CACHE_TTL", "157680000"))  # default: 5 years
_CACHE_DIR = os.environ.get("CACHE_DIR", os.path.join(os.getcwd(), "cache"))


# ---------------------------------------------------------------------------
# Key builder
# ---------------------------------------------------------------------------

def make_key(req: EvaluationRequest, provider: str, model: str) -> str:
    """Build a human-readable, searchable cache key."""
    prefix = re.sub(r"[^a-z0-9]+", "-", (req.tag or provider).lower()).strip("-")
    slug   = re.sub(r"[^a-z0-9]+", "-", req.input.lower())[:40].strip("-")

    fp_src = json.dumps({
        "input":             req.input,
        "result_ids":        sorted(str(r.id) for r in req.results),
        "prompt":            req.prompt or "",
        "prompt_preset":     req.prompt_preset or "",
        "provider":          provider,
        "model":             model,
        "response_language": req.response_language or "",
        "system_prompt_ver":    _SYSTEM_PROMPT_VER,
        "presets_ver":          _PRESETS_VER,
        "prompt_builder_ver":   _PROMPT_BUILDER_VER,
    }, sort_keys=True)
    fp = hashlib.sha256(fp_src.encode()).hexdigest()[:8]

    return f"keyless_eval:{prefix}:{slug}:{fp}"


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class _FileCache:
    def __init__(self, cache_dir: str, ttl: int) -> None:
        self._dir = cache_dir
        self._ttl = ttl
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        # Replace ':' with '_' so the key is a safe filename
        return os.path.join(self._dir, key.replace(":", "_") + ".json")

    def get(self, key: str) -> dict | None:
        try:
            with open(self._path(key)) as f:
                data = json.load(f)
            if self._ttl > 0 and time.time() - data["_at"] > self._ttl:
                os.unlink(self._path(key))
                return None
            return data["v"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: dict) -> None:
        with open(self._path(key), "w", encoding="utf-8") as f:
            json.dump({"_at": time.time(), "v": value}, f, ensure_ascii=False)


class _RedisCache:
    def __init__(self, url: str, ttl: int) -> None:
        import redis  # type: ignore
        self._r   = redis.from_url(url, decode_responses=True)
        self._ttl = ttl

    def get(self, key: str) -> dict | None:
        raw = self._r.get(key)
        return json.loads(raw) if raw else None

    def set(self, key: str, value: dict) -> None:
        raw = json.dumps(value, ensure_ascii=False)
        if self._ttl > 0:
            self._r.setex(key, self._ttl, raw)
        else:
            self._r.set(key, raw)


# ---------------------------------------------------------------------------
# Singleton backend (lazy init)
# ---------------------------------------------------------------------------

_backend: _FileCache | _RedisCache | None = None
_backend_init = False


def _get_backend() -> _FileCache | _RedisCache | None:
    global _backend, _backend_init
    if _backend_init:
        return _backend
    _backend_init = True

    if _CACHE_TTL == 0:
        _log.info("cache disabled (CACHE_TTL=0)")
        return None

    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        try:
            _backend = _RedisCache(redis_url, _CACHE_TTL)
            _log.info("cache backend=redis ttl=%ds", _CACHE_TTL)
            return _backend
        except Exception as e:
            _log.warning("Redis unavailable (%s), falling back to file cache", e)

    _backend = _FileCache(_CACHE_DIR, _CACHE_TTL)
    _log.info("cache backend=file dir=%s ttl=%ds", _CACHE_DIR, _CACHE_TTL)
    return _backend


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cache_get(
    req: EvaluationRequest, provider: str, model: str
) -> tuple[EvaluationResponse, str] | None:
    """Return (cached_response, cache_key) on hit, None on miss."""
    backend = _get_backend()
    if backend is None:
        return None
    key  = make_key(req, provider, model)
    data = backend.get(key)
    if data is None:
        return None
    try:
        return EvaluationResponse.model_validate(data), key
    except Exception as e:
        _log.warning("cache deserialize error for key %s: %s", key, e)
        return None


def cache_set(
    req: EvaluationRequest, provider: str, model: str, response: EvaluationResponse
) -> str:
    """Store response in cache. Returns the key used (empty string if caching disabled)."""
    backend = _get_backend()
    if backend is None:
        return ""
    key = make_key(req, provider, model)
    try:
        backend.set(key, response.model_dump())
    except Exception as e:
        _log.warning("cache write error for key %s: %s", key, e)
    return key
