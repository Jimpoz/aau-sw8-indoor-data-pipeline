from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(slots=True)
class RoomImageInput:
    source_name: str
    frame: np.ndarray = field(repr=False)
