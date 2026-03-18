lsof -ti:8510 -sTCP:LISTEN|xargs kill
uv run python main.py &

