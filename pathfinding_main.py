from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from db import close_neo4j, initialize_neo4j_schema
from routes_pathfinding import router as pathfinding_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_neo4j_schema()
    yield
    close_neo4j()


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    lifespan=lifespan,
)

app.include_router(pathfinding_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
