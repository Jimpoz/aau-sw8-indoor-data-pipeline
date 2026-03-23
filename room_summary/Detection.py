from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Detection:
    class_id: int
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]
