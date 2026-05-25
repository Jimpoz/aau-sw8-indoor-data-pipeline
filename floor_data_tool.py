#!/usr/bin/env python
"""Run the local Floor Data Tool."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROJECT_ENV = ROOT / "proj"
PROJECT_PYTHON = ROOT / "proj" / "bin" / "python"
if PROJECT_PYTHON.exists() and Path(sys.prefix).resolve() != PROJECT_ENV.resolve():
    os.execv(str(PROJECT_PYTHON), [str(PROJECT_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

BACKEND_DIR = ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from local_server import serve  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
