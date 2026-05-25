"""Small HTTP client for the Map API used by the local pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


def normalize_server_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        raise ValueError("Map API URL is required")
    return value


def api_url(server_url: str, path: str) -> str:
    base = normalize_server_url(server_url)
    if base.endswith("/api/v1"):
        return f"{base}{path}"
    return f"{base}/api/v1{path}"


def auth_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "ngrok-skip-browser-warning": "true",
    }
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def fetch_json(
    server_url: str,
    path: str,
    *,
    token: str | None = None,
    timeout: float = 30,
) -> Any:
    request = Request(api_url(server_url, path), headers=auth_headers(token))
    with urlopen(request, timeout=timeout) as response:
        if response.status == 204:
            return None
        return json.loads(response.read().decode("utf-8"))


def post_json(
    server_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    timeout: float = 180,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        **auth_headers(token),
        "Content-Type": "application/json",
    }
    request = Request(api_url(server_url, path), data=body, headers=headers, method="POST")
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="ignore")
        data = json.loads(text) if text else None
        return {"status": response.status, "body": data}


@dataclass(frozen=True)
class MapApiClient:
    server_url: str
    token: str | None = None

    @classmethod
    def from_body(cls, body: dict[str, Any]) -> "MapApiClient":
        return cls(
            server_url=str(body.get("server_url") or body.get("serverUrl") or ""),
            token=body.get("token") or None,
        )

    def get(self, path: str, *, timeout: float = 30) -> Any:
        return fetch_json(self.server_url, path, token=self.token, timeout=timeout)

    def post(self, path: str, payload: dict[str, Any], *, timeout: float = 180) -> dict[str, Any]:
        return post_json(self.server_url, path, payload, token=self.token, timeout=timeout)
