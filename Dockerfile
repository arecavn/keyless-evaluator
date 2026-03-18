# =============================================================================
# keyless-evaluator
# Layer order optimized for cache: system → Python deps → Playwright → source
# When only api/ or main.py changes, all layers above "COPY api/" are reused.
# =============================================================================

FROM python:3.13-slim-bookworm

# ── Layer 1: OS packages ──────────────────────────────────────────────────────
# Changes almost never. Install Chromium runtime libs + Xvfb virtual display.
# Xvfb lets Chromium run "headed" on a virtual screen — avoids Cloudflare
# bot-detection that specifically targets --headless mode.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    libx11-6 libx11-xcb1 libxcb1 libxext6 libxrender1 libxi6 \
    xvfb \
    fonts-noto fonts-liberation \
    wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 2: uv ───────────────────────────────────────────────────────────────
# Changes almost never.
RUN pip install --no-cache-dir uv

WORKDIR /app

# ── Layer 3: dependency manifests ─────────────────────────────────────────────
# Invalidates when pyproject.toml or uv.lock changes (i.e. new/updated deps).
# README.md is needed by setuptools to build the package metadata.
COPY pyproject.toml uv.lock uv.toml README.md ./

# ── Layer 4: install external Python deps ─────────────────────────────────────
# --no-install-workspace skips building the local package (which needs api/).
# All third-party wheels are downloaded and cached here.
# Invalidates only when uv.lock changes.
RUN uv sync --frozen --no-dev --no-install-workspace

# ── Layer 5a: install Google Chrome (x86_64 only) ─────────────────────────────
# Real Chrome bypasses Cloudflare bot-detection far better than Playwright's
# bundled Chromium. ARM64 skips this (Google does not publish ARM64 Chrome).
# The evaluator tries channel="chrome" first and falls back to Chromium.
RUN if [ "$(uname -m)" = "x86_64" ]; then \
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
        http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && apt-get install -y --no-install-recommends google-chrome-stable && \
    rm -rf /var/lib/apt/lists/*; \
fi

# ── Layer 5b: install Playwright's bundled Chromium (fallback / ARM64) ────────
# Used on ARM64 (Mac Docker), and as Playwright automation driver on all arches.
RUN .venv/bin/python -m playwright install --with-deps chromium

# ── Layer 6: application source ───────────────────────────────────────────────
# Changes on every code edit. All layers above are reused on code-only changes.
COPY api/ ./api/
COPY main.py ./
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── Layer 7: install local package ───────────────────────────────────────────
# Now that api/ exists, install the local package. No downloads — just links
# the package. Very fast (< 1 s).
RUN uv sync --frozen --no-dev

# ── Layer 8: runtime dirs ─────────────────────────────────────────────────────
RUN mkdir -p /data/logs /data/chatgpt-profile /data/gemini-profile

# ── Runtime env ──────────────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    # Run Chromium "headed" on the Xvfb virtual display — not true headless.
    CHATGPT_WEB_HEADLESS=0 \
    DISPLAY=:99 \
    CHATGPT_PROFILE_DIR=/data/chatgpt-profile \
    GEMINI_PROFILE_DIR=/data/gemini-profile

EXPOSE 8510

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uv", "run", "python", "main.py", "--host", "0.0.0.0", "--port", "8510"]
