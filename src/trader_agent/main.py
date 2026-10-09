from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .env import load_env
from .utils.logging import setup_logging


def _parse_since(value: str) -> datetime:
    if value.endswith("h") and value[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(hours=int(value[:-1]))
    if value.endswith("d") and value[:-1].isdigit():
        return datetime.now(timezone.utc) - timedelta(days=int(value[:-1]))
    try:
        since = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("24h, 7d veya YYYY-MM-DD biçimini kullanın.")
    return since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since


def main() -> None:
    parser = argparse.ArgumentParser(prog="trader-agent")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Ortam ayarlarının dosyası")
    commands = parser.add_subparsers(dest="command", required=True)
    engine = commands.add_parser("engine", help="Engine HTTP servisini başlat")
    engine.add_argument("--host")
    engine.add_argument("--port", type=int)
    digest = commands.add_parser("daily-digest", help="Engine'de haber özetlerini yenile")
    digest.add_argument("--since", type=_parse_since)
    commands.add_parser("prefetch-market", help="Engine'in piyasa önbelleğini doldur")
    args = parser.parse_args()
    load_env(args.env_file)
    directory = os.environ.get("TRADER_LOGS_DIR") if args.command == "engine" else None
    setup_logging(Path(directory).expanduser() if directory else None)

    if args.command == "engine":
        import uvicorn
        from .engine.api import create_app

        host = args.host if args.host is not None else os.environ["ENGINE_HOST"]
        port = args.port if args.port is not None else int(os.environ["ENGINE_PORT"])
        api_key = os.environ.get("ENGINE_API_KEY", "")
        uvicorn.run(create_app(api_key=api_key), host=host, port=port)
    else:
        asyncio.run(_run_job(args))


async def _run_job(args: argparse.Namespace) -> None:
    from .client import EngineClient

    url = os.environ.get("ENGINE_URL")
    if not url:
        raise RuntimeError(
            "ENGINE_URL zorunlu. Engine adresini .env veya ortam değişkeni ile tanımlayın."
        )
    api_key = os.environ.get("ENGINE_API_KEY", "")
    async with EngineClient(url=url, api_key=api_key) as client:
        if args.command == "daily-digest":
            result = await client.daily_digest(args.since)
        else:
            result = await client.prefetch_market()
    print(json.dumps(result, ensure_ascii=False, indent=2))
