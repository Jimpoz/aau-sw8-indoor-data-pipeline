from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .LoadedPage import LoadedPage


@dataclass(slots=True)
class SourceDocument:
    source_name: str
    source_type: str
    source_path: str | None
    metadata: dict[str, Any]
    pages: list[LoadedPage]
