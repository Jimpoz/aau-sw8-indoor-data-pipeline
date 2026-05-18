from __future__ import annotations

import os

from fastapi import FastAPI

from routes_dxf import router as dxf_router


app = FastAPI(
    title=os.getenv("API_TITLE", "Indoor Data Pipeline"),
    version=os.getenv("API_VERSION", "1.0.0"),
)

app.include_router(dxf_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
