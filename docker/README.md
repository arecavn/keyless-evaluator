# Docker Deployment

## Quick start (API providers only — no browser login needed)

```bash
# Copy .env.example → .env and add your API keys
cp .env.example .env

# Build + start
docker compose up -d

# Check health
curl http://localhost:8510/health
```

---

## ChatGPT Web / Gemini Web — Google Login Setup

These providers use a persistent Chrome profile with saved cookies.
**You cannot log in via Google OAuth inside a headless Docker container.**
The only working approach is:

1. **Login on your Mac** (visible browser, once)
2. **Copy the saved profile to the server**
3. Docker runs headless from then on

### Step 1 — Login on Mac

```bash
# Force visible browser window
CHATGPT_WEB_LOGIN=1 uv run keyless-eval eval \
    -q "test" -f example_results.json -p chatgpt_web
# A Chrome window opens → log in with Google → close the window after landing on ChatGPT
```

The session is now saved at `~/.local/share/keyless-eval/chatgpt/`.

For Gemini Web, do the same with `-p gemini_web`.
The session is saved at `~/.local/share/keyless-eval/gemini/`.

### Step 2 — Copy profile to server

```bash
# ChatGPT profile
rsync -avz ~/.local/share/keyless-eval/chatgpt/ \
    user@server:/opt/keyless-eval/chatgpt-profile/

# Gemini profile (if using gemini_web)
rsync -avz ~/.local/share/keyless-eval/gemini/ \
    user@server:/opt/keyless-eval/gemini-profile/
```

### Step 3 — Mount profiles as Docker volumes

Edit `docker-compose.yml` to use bind mounts pointing to the copied profiles:

```yaml
volumes:
  - /opt/keyless-eval/chatgpt-profile:/data/chatgpt-profile
  - /opt/keyless-eval/gemini-profile:/data/gemini-profile
  - ./logs:/data/logs
```

Then restart:

```bash
docker compose up -d
```

### Session refresh

ChatGPT sessions expire after ~30 days. Repeat steps 1-2 to refresh.
You don't need to rebuild the image — just rsync the new profile and restart.

---

## Environment Variables

| Variable              | Default                      | Description                            |
|-----------------------|------------------------------|----------------------------------------|
| `GEMINI_API_KEY`      | —                            | Gemini API key (free from AI Studio)   |
| `OPENAI_API_KEY`      | —                            | OpenAI API key                         |
| `ANTHROPIC_API_KEY`   | —                            | Anthropic API key                      |
| `CHATGPT_PROFILE_DIR` | `/data/chatgpt-profile`      | Chrome profile for chatgpt_web         |
| `GEMINI_PROFILE_DIR`  | `/data/gemini-profile`       | Chrome profile for gemini_web          |
| `CHATGPT_WEB_HEADLESS`| `1`                          | Always `1` in Docker                   |
| `ALLOWED_ORIGINS`     | `*`                          | CORS origins (comma-separated)         |
| `CHATGPT_URL`         | `https://chatgpt.com/`       | Override ChatGPT URL (e.g. project)    |
