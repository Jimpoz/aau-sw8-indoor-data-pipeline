from __future__ import annotations

import os

import uvicorn

from services.floorplan_translator_service import app


def main() -> None:
    host = os.getenv("FLOORPLAN_SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("FLOORPLAN_SERVICE_PORT", "8010"))
    reload_enabled = os.getenv("FLOORPLAN_SERVICE_RELOAD", "false").strip().lower() in {"1", "true", "yes", "on"}
    uvicorn.run("main:app", host=host, port=port, reload=reload_enabled)


if __name__ == "__main__":
    main()
