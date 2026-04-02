from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AnalyzedConnection:
    id: str
    display_name: str
    space_type: str
    connects: list[str]
    centroid_x: float
    centroid_y: float
    polygon: list[list[float]]
    is_accessible: bool = True
    tags: list[str] = field(default_factory=list)
    transition_time_s: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_centroid_px: tuple[float, float] | None = field(default=None, repr=False)

    def to_response_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "space_type": self.space_type,
            "connects": self.connects,
            "centroid_x": self.centroid_x,
            "centroid_y": self.centroid_y,
            "polygon": self.polygon,
            "is_accessible": self.is_accessible,
            "tags": self.tags,
            "transition_time_s": self.transition_time_s,
            "metadata": self.metadata,
        }

    def to_map_import_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "space_type": self.space_type,
            "connects": self.connects,
            "centroid_x": self.centroid_x,
            "centroid_y": self.centroid_y,
            "polygon": self.polygon,
            "is_accessible": self.is_accessible,
            "tags": self.tags,
            "transition_time_s": self.transition_time_s,
        }
