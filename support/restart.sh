lsof -ti:8000 -sTCP:LISTEN|xargs kill
uv run main.py &

