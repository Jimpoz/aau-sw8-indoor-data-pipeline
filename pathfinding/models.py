from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class FastestPathResult:
    path: List[str]
    cost: float


@dataclass(frozen=True)
class SpaceRect:
    name: str
    kind: str
    floor: int
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class StatePosition:
    x: float
    y: float
    floor: Optional[int]


@dataclass
class StateGraph:
    adjacency: Dict[str, List[Tuple[str, float]]]
    lookup: Dict[str, str]
