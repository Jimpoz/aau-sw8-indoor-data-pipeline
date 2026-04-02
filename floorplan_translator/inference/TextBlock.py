from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TextBlock:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]
