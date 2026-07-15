"""Convenience shim — the real entrypoint is app.main.

Run the service with:  uv run python -m app.main
"""
from dotenv import load_dotenv
load_dotenv()
from app.main import main as _amain
import asyncio


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
