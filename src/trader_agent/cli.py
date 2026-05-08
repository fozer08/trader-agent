from __future__ import annotations

import asyncio
import sys

from .env import load_env
from .utils.logging import setup_logging


def main() -> None:
    load_env()
    setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "telegram":
        from .channels.telegram import run
        asyncio.run(run())
    elif cmd == "serve":
        import uvicorn
        from .server.app import app
        uvicorn.run(app, host="127.0.0.1", port=8000)
    elif cmd == "chat":
        from .channels.cli import run
        asyncio.run(run())
    else:
        print("Kullanım: trader-agent [telegram|serve|chat]")
        sys.exit(1)
