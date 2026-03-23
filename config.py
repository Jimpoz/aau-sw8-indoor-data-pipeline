from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def _load_default_env_files() -> None:
    configured_path = os.getenv("APP_CONFIG_PATH")
    candidate_paths = [Path(configured_path)] if configured_path else []
    candidate_paths.extend([Path("app.cfg"), Path(".env")])

    seen_paths: set[Path] = set()
    for candidate_path in candidate_paths:
        if candidate_path in seen_paths:
            continue
        seen_paths.add(candidate_path)
        _load_env_file(candidate_path)


def _resolve_neo4j_auth() -> tuple[str, str]:
    neo4j_user = os.getenv("NEO4J_USER")
    neo4j_password = os.getenv("NEO4J_PASSWORD")
    neo4j_auth = os.getenv("NEO4J_AUTH")

    if (not neo4j_user or not neo4j_password) and neo4j_auth and "/" in neo4j_auth:
        parsed_user, parsed_password = neo4j_auth.split("/", 1)
        neo4j_user = neo4j_user or parsed_user
        neo4j_password = neo4j_password or parsed_password

    return neo4j_user or "neo4j", neo4j_password or "password"


_load_default_env_files()
_neo4j_user, _neo4j_password = _resolve_neo4j_auth()


@dataclass(frozen=True, slots=True)
class Settings:
    api_title: str
    api_version: str
    backend_port: int
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str


settings = Settings(
    api_title=os.getenv("API_TITLE", "Indoor Data Pipeline Backend"),
    api_version=os.getenv("API_VERSION", "1.0.0"),
    backend_port=int(os.getenv("BACKEND_PORT", "6969")),
    neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    neo4j_user=_neo4j_user,
    neo4j_password=_neo4j_password,
)
