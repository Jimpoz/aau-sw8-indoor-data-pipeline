from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GeometryConfig:
    blur_kernel: int = 3
    wall_open_kernel: int = 3
    wall_dilate_kernel: int = 3
    wall_component_min_area: int = 90
    opening_close_kernel: int = 29
    opening_touch_kernel: int = 11
    opening_min_area: int = 60
    opening_max_area: int = 15000
    space_min_area_ratio: float = 0.0008
    polygon_epsilon_ratio: float = 0.008
    corridor_min_door_count: int = 3
    corridor_min_area_ratio: float = 0.035
    room_label_min_confidence: float = 45.0
