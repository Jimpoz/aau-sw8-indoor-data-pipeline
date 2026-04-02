from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class InputConfig:
    pdf_dpi: int = 400
    processing_max_dimension: int = 3200
    crop_top_fraction: float = 0.02
    crop_right_fraction: float = 0.02
    crop_bottom_fraction: float = 0.16
    crop_left_fraction: float = 0.02
    bbox_padding_px: int = 20
