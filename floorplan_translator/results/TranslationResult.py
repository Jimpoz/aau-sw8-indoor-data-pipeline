from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .PageAnalysis import PageAnalysis


@dataclass(slots=True)
class TranslationResult:
    source: dict[str, Any]
    warnings: list[str]
    campus: dict[str, Any]
    building: dict[str, Any]
    floors: list[PageAnalysis]
    map_import: dict[str, Any] | None = None
    backend_import: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "source": self.source,
            "warnings": self.warnings,
            "campus": self.campus,
            "building": self.building,
            "floors": [floor.to_response_dict() for floor in self.floors],
        }
        if self.map_import is not None:
            payload["map_import"] = self.map_import
        if self.backend_import is not None:
            payload["backend_import"] = self.backend_import
        return payload
