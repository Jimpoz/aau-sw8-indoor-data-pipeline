from __future__ import annotations

from typing import Any

from .models import FastestPathResult
from .pathfinder import DijkstraPathfinder
from .query_runner import Neo4jQueryRunner
from .renderers import PathMapSvgRenderer, PathSvgRenderer
from .repository import PathfindingRepository


class PathfindingService:
    """Facade that coordinates pathfinding data access and rendering."""

    def __init__(self, conn: Any) -> None:
        self._repository = PathfindingRepository(Neo4jQueryRunner(conn))
        self._pathfinder = DijkstraPathfinder()
        self._path_svg_renderer = PathSvgRenderer()
        self._map_svg_renderer = PathMapSvgRenderer()

    def list_all_names(self) -> list[str]:
        return self._repository.list_state_names()

    def list_room_names_by_floor(self, floor: int) -> list[str]:
        return self._repository.list_room_names_by_floor(floor)

    def find_fastest_path(self, start_name: str, end_name: str = "outside") -> FastestPathResult:
        # Build graph from Neo4j on each request so routing reflects latest persisted topology.
        graph = self._repository.build_state_graph()
        return self._pathfinder.find_fastest_path(graph, start_name, end_name)

    def build_fastest_path_svg(
        self,
        start_name: str,
        end_name: str,
        result: FastestPathResult,
    ) -> str:
        return self._path_svg_renderer.render(start_name, end_name, result)

    def build_fastest_path_map_svg(
        self,
        start_name: str,
        end_name: str,
        result: FastestPathResult,
    ) -> str:
        # Map rendering needs both space geometry and state coordinates from Neo4j.
        rects_by_floor = self._repository.load_map_rects()
        state_positions = self._repository.load_state_positions()
        return self._map_svg_renderer.render(
            start_name=start_name,
            end_name=end_name,
            result=result,
            rects_by_floor=rects_by_floor,
            state_positions=state_positions,
        )
