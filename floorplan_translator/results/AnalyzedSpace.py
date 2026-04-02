from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AnalyzedSpace:
    id: str
    display_name: str
    space_type: str
    centroid_x: float
    centroid_y: float
    polygon: list[list[float]]
    width_m: float | None
    length_m: float | None
    area_m2: float | None
    is_accessible: bool = True
    is_navigable: bool = True
    is_outdoor: bool = False
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_polygon_px: list[list[float]] = field(default_factory=list, repr=False)
    raw_centroid_px: tuple[float, float] | None = field(default=None, repr=False)
    area_ratio: float = field(default=0.0, repr=False)
    region_label: int = field(default=0, repr=False)
    door_count: int = field(default=0, repr=False)

    def apply_door_count(self, door_count: int, resolved_space_type: str | None = None) -> None:
        self.door_count = door_count
        if resolved_space_type is not None:
            self.space_type = resolved_space_type
        translator_metadata = self.metadata.setdefault("translator", {})
        translator_metadata["door_count"] = door_count
        translator_metadata["raw_polygon_area_ratio"] = self.area_ratio

    def to_response_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "space_type": self.space_type,
            "centroid_x": self.centroid_x,
            "centroid_y": self.centroid_y,
            "polygon": self.polygon,
            "width_m": self.width_m,
            "length_m": self.length_m,
            "area_m2": self.area_m2,
            "is_accessible": self.is_accessible,
            "is_navigable": self.is_navigable,
            "is_outdoor": self.is_outdoor,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    def to_map_import_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "short_name": None,
            "space_type": self.space_type,
            "centroid_x": self.centroid_x,
            "centroid_y": self.centroid_y,
            "polygon": self.polygon,
            "width_m": self.width_m,
            "length_m": self.length_m,
            "area_m2": self.area_m2,
            "is_accessible": self.is_accessible,
            "is_navigable": self.is_navigable,
            "is_outdoor": self.is_outdoor,
            "capacity": None,
            "tags": self.tags,
            "metadata": self.metadata,
            "subspaces": [],
        }
