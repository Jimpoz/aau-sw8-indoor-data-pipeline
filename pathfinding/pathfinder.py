from __future__ import annotations

import heapq
import math
from typing import Dict, List, Tuple

from .models import FastestPathResult, StateGraph


class DijkstraPathfinder:
    """Shortest path calculator over a directed weighted state graph."""

    @staticmethod
    def _normalize(text: str) -> str:
        return text.strip().lower()

    def find_fastest_path(
        self,
        graph: StateGraph,
        start_name: str,
        end_name: str = "outside",
    ) -> FastestPathResult:
        start = graph.lookup.get(self._normalize(start_name))
        end = graph.lookup.get(self._normalize(end_name))
        if start is None:
            raise ValueError(f"Unknown start name: {start_name}")
        if end is None:
            raise ValueError(f"Unknown end name: {end_name}")

        # Dijkstra setup, best-known distance map + predecessor chain.
        dist: Dict[str, float] = {node: math.inf for node in graph.adjacency}
        prev: Dict[str, str] = {}
        dist[start] = 0.0
        queue: List[Tuple[float, str]] = [(0.0, start)]

        # Dijkstra loop, expand the currently cheapest frontier node.
        while queue:
            current_dist, node = heapq.heappop(queue)
            if current_dist > dist[node]:
                continue
            if node == end:
                break
            for next_node, weight in graph.adjacency[node]:
                alternative = current_dist + weight
                if alternative < dist[next_node]:
                    dist[next_node] = alternative
                    prev[next_node] = node
                    heapq.heappush(queue, (alternative, next_node))

        if not math.isfinite(dist[end]):
            raise ValueError(f"No path found from {start_name} to {end_name}")

        # Reconstruct path from end -> start using the predecessor chain.
        path: List[str] = [end]
        while path[-1] != start:
            path.append(prev[path[-1]])
        path.reverse()
        return FastestPathResult(path=path, cost=round(dist[end], 3))
