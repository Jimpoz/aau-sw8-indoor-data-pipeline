from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .AnalyzedConnection import AnalyzedConnection
from .AnalyzedSpace import AnalyzedSpace


@dataclass(slots=True)
class PageAnalysis:
    id: str
    floor_index: int
    display_name: str
    source_page: int
    coordinate_unit: str
    scale_meters_per_pixel: float | None
    scale_source: str
    footprint: list[list[float]]
    walls: list[list[list[float]]]
    spaces: list[AnalyzedSpace]
    doors: list[AnalyzedConnection]
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_response_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "floor_index": self.floor_index,
            "display_name": self.display_name,
            "source_page": self.source_page,
            "coordinate_unit": self.coordinate_unit,
            "scale": {
                "meters_per_pixel": self.scale_meters_per_pixel,
                "source": self.scale_source,
            },
            "footprint": self.footprint,
            "walls": self.walls,
            "spaces": [space.to_response_dict() for space in self.spaces],
            "doors": [door.to_response_dict() for door in self.doors],
            "warnings": self.warnings,
            "debug": self.debug,
            "metadata": self.metadata,
        }
