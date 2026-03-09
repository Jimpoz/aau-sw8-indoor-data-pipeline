from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Tuple

from .models import SpaceRect, StateGraph, StatePosition
from .query_runner import Neo4jQueryRunner


class PathfindingRepository:
    """Neo4j-backed data access for pathfinding and map rendering."""

    def __init__(self, query_runner: Neo4jQueryRunner) -> None:
        self._query_runner = query_runner

    @staticmethod
    def _normalize(text: str) -> str:
        return text.strip().lower()

    @staticmethod
    def _add_lookup(lookup: Dict[str, str], alias: Any, target: str) -> None:
        if alias is None:
            return
        value = str(alias).strip()
        if not value:
            return
        lookup[PathfindingRepository._normalize(value)] = target

    def list_state_names(self) -> List[str]:
        rows = self._query_runner.run("MATCH (s:State) RETURN s.name AS name")
        names = sorted({str(row["name"]) for row in rows if row["name"] is not None})
        if any(name.startswith("outside_") for name in names) and "outside" not in names:
            names.append("outside")
        return names

    def list_room_names_by_floor(self, floor: int) -> List[str]:
        rows = self._query_runner.run(
            """
            MATCH (g:GeneralSpace)-[:HAS_FLOOR]->(f:Floor {id: $floor})
            RETURN g.name AS name
            ORDER BY g.name
            """,
            floor=floor,
        )
        return [str(row["name"]) for row in rows if row["name"] is not None]

    def build_state_graph(self) -> StateGraph:
        graph: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        lookup: Dict[str, str] = {}

        # Pull all state identifiers so Dijkstra can run in-memory on a normalized node set.
        state_rows = self._query_runner.run(
            """
            MATCH (s:State)
            RETURN s.name AS name, s.id AS id, s.gml_id AS gml_id
            """,
        )
        for row in state_rows:
            primary = row["name"] or row["id"] or row["gml_id"]
            if primary is None:
                continue
            node = str(primary)
            graph[node]
            self._add_lookup(lookup, row["name"], node)
            self._add_lookup(lookup, row["id"], node)
            self._add_lookup(lookup, row["gml_id"], node)

        # PATH relationships carry the edge weights used by Dijkstra.
        path_rows = self._query_runner.run(
            """
            MATCH (a:State)-[p:PATH]->(b:State)
            RETURN
              coalesce(a.name, a.id, a.gml_id) AS a_name,
              coalesce(b.name, b.id, b.gml_id) AS b_name,
              coalesce(p.cost, 1.0) AS cost
            """,
        )
        for row in path_rows:
            a_name = row["a_name"]
            b_name = row["b_name"]
            if a_name is None or b_name is None:
                continue
            graph[str(a_name)].append((str(b_name), float(row["cost"])))

        # DUALITY allows API users to pass room/space ids and still resolve to State nodes.
        dual_rows = self._query_runner.run(
            """
            MATCH (s:State)-[:DUALITY]-(x)
            WHERE x:GeneralSpace OR x:TransitionSpace
            RETURN
              coalesce(s.name, s.id, s.gml_id) AS state_key,
              x.name AS x_name,
              x.id AS x_id,
              x.gml_id AS x_gml_id
            """,
        )
        for row in dual_rows:
            state_key = row["state_key"]
            if state_key is None:
                continue
            target = str(state_key)
            graph[target]
            self._add_lookup(lookup, row["x_name"], target)
            self._add_lookup(lookup, row["x_id"], target)
            self._add_lookup(lookup, row["x_gml_id"], target)

        outside_nodes = [name for name in graph if name.startswith("outside_")]
        if outside_nodes:
            # Keep one virtual outside hub so callers can route to "outside".
            hub = "outside"
            graph[hub]
            self._add_lookup(lookup, hub, hub)
            for node in outside_nodes:
                graph[hub].append((node, 0.0))
                graph[node].append((hub, 0.0))

        return StateGraph(adjacency=dict(graph), lookup=lookup)

    def load_map_rects(self) -> Dict[int, List[SpaceRect]]:
        # Geometry is fetched from Neo4j because route maps are rendered from stored space bounds.
        rows = self._query_runner.run(
            """
            MATCH (g:GeneralSpace)-[:HAS_FLOOR]->(f:Floor)
            RETURN
              g.name AS name,
              g.kind AS kind,
              toFloat(g.x) AS x,
              toFloat(g.y) AS y,
              toFloat(g.width) AS width,
              toFloat(g.height) AS height,
              toInteger(f.id) AS floor
            ORDER BY floor, kind, name
            """,
        )
        result: Dict[int, List[SpaceRect]] = defaultdict(list)
        for row in rows:
            floor = row["floor"]
            if floor is None:
                continue
            floor_id = int(floor)
            result[floor_id].append(
                SpaceRect(
                    name=str(row["name"] or ""),
                    kind=str(row["kind"] or "room"),
                    floor=floor_id,
                    x=float(row["x"] or 0.0),
                    y=float(row["y"] or 0.0),
                    width=float(row["width"] or 0.0),
                    height=float(row["height"] or 0.0),
                )
            )
        return dict(result)

    def load_state_positions(self) -> Dict[str, StatePosition]:
        # State coordinates are used to project the computed path onto the map image.
        rows = self._query_runner.run(
            """
            MATCH (s:State)
            OPTIONAL MATCH (s)-[:HAS_FLOOR]->(f:Floor)
            RETURN
              coalesce(s.name, s.id, s.gml_id) AS node_key,
              toFloat(s.x) AS x,
              toFloat(s.y) AS y,
              collect(DISTINCT toInteger(f.id)) AS floors
            """,
        )
        result: Dict[str, StatePosition] = {}
        for row in rows:
            key = row["node_key"]
            if key is None:
                continue
            floors = [int(f) for f in (row["floors"] or []) if f is not None]
            floor = min(floors) if floors else None
            result[str(key)] = StatePosition(
                x=float(row["x"] or 0.0),
                y=float(row["y"] or 0.0),
                floor=floor,
            )
        return result
