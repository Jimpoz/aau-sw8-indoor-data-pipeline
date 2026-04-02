from __future__ import annotations

import json
import socket
from typing import Any
from urllib import error, parse, request


def import_map_to_backend(map_import: dict[str, Any], base_url: str, timeout_seconds: float = 60.0) -> dict[str, Any]:
    campus_id = map_import["campus"]["id"]
    parsed_base_url = parse.urlparse(base_url)
    if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
        raise ValueError(
            f"backend_import_base_url must be a full http(s) URL, got {base_url!r}. "
            "If you only want the JSON output, set import_to_backend to false."
        )
    url = f"{base_url.rstrip('/')}/campuses/{campus_id}/import"
    payload = json.dumps(map_import).encode("utf-8")
    req = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Backend import failed with HTTP {exc.code} for {url}: {response_body or exc.reason}"
        ) from exc
    except error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise TimeoutError(
                f"Backend import timed out after {timeout_seconds:.0f}s for {url}. "
                "If you only want the translated JSON, set import_to_backend to false."
            ) from exc
        raise ConnectionError(f"Backend import request failed for {url}: {reason}") from exc
    except TimeoutError as exc:
        raise TimeoutError(
            f"Backend import timed out after {timeout_seconds:.0f}s for {url}. "
            "If you only want the translated JSON, set import_to_backend to false."
        ) from exc
    return json.loads(raw_body) if raw_body else {"status": "ok"}
