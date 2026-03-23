from __future__ import annotations

import time

from neo4j import Driver, GraphDatabase

from config import settings


neo4j_driver: Driver = GraphDatabase.driver(
    settings.neo4j_uri,
    auth=(settings.neo4j_user, settings.neo4j_password),
)


def initialize_neo4j_schema(retries: int = 10, retry_delay_seconds: float = 2.0) -> None:
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            with neo4j_driver.session() as session:
                session.run("RETURN 1").consume()
            return
        except Exception as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(retry_delay_seconds)

    if last_error is not None:
        raise RuntimeError(f"Could not connect to Neo4j at {settings.neo4j_uri}") from last_error


def close_neo4j() -> None:
    neo4j_driver.close()
