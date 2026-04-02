from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(slots=True)
class LoadedPage:
    page_index: int
    image: np.ndarray
    metadata: dict[str, Any]
