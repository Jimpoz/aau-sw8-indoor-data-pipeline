from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ScaleConfig:
    default_meters_per_pixel: float | None = 0.05
    use_default_scale_as_fallback: bool = False
