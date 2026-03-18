lsof -ti:8510 -sTCP:LISTEN|xargs kill
uv run python main.py --host 0.0.0.0 --port 8510 &

