"""Start the Keyless Evaluator API server.

Usage:
    uv run main.py
    uv run main.py --host 0.0.0.0 --port 8080
"""

import os
import sys

# Add api/ to path so flat modules (server, cli, models, …) are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))

from dotenv import load_dotenv
load_dotenv()

import uvicorn

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Keyless Evaluator API server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8510, help="Bind port (default: 8510)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    args = parser.parse_args()

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
