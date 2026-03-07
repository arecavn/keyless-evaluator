"""Start the Keyless Evaluator API server.

Usage:
    uv run main.py
    uv run main.py --host 0.0.0.0 --port 8080
"""

import uvicorn

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Keyless Evaluator API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    args = parser.parse_args()

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
