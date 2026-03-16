lsof -ti:8510 -sTCP:LISTEN|xargs kill
uv run main.py &

