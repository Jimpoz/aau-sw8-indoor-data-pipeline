from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Dict, List, Tuple

from .models import SpaceRect, StateGraph, StatePosition
from .query_runner import Neo4jQueryRunner


class PathfindingRepository:
    """Neo4j-backed data access for room/door pathfinding and map rendering."""

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
        rows = self._query_runner.run(
            """
            MATCH (r:Room)
            RETURN coalesce(r.name, toString(r.id)) AS name
            ORDER BY name
            """,
        )
        names = sorted({str(row["name"]) for row in rows if row["name"] is not None})
        if self._has_outside_room_connection() and "outside" not in names:
            names.append("outside")
        return names

    def list_room_names_by_floor(self, floor: int) -> List[str]:
        rows = self._query_runner.run(
            """
            MATCH (g:Room)
            WHERE toInteger(g.floor) = $floor
              AND toLower(trim(coalesce(g.kind, ""))) = "room"
            RETURN coalesce(g.name, toString(g.id)) AS name
            ORDER BY name
            """,
            floor=floor,
        )
        return [str(row["name"]) for row in rows if row["name"] is not None]

    def build_state_graph(self) -> StateGraph:
        graph: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        lookup: Dict[str, str] = {}

        room_rows = self._query_runner.run(
            """
            MATCH (r:Room)
            RETURN coalesce(r.name, toString(r.id)) AS node_name, r.name AS name, r.id AS id
            """,
        )
        for row in room_rows:
            node_name = row["node_name"]
            if node_name is None:
                continue
            node = str(node_name)
            graph[node]
            self._add_lookup(lookup, row["name"], node)
            self._add_lookup(lookup, row["id"], node)
            self._add_lookup(lookup, node, node)

        edge_rows = self._query_runner.run(
            """
            MATCH (d:Door)-[:CONNECTS_TO]-(a:Room),
                  (d)-[:CONNECTS_TO]-(b:Room)
            WHERE coalesce(a.name, toString(a.id)) <> coalesce(b.name, toString(b.id))
            RETURN DISTINCT
              coalesce(a.name, toString(a.id)) AS a_name,
              coalesce(b.name, toString(b.id)) AS b_name,
              d.name AS door_name
            """,
        )
        for row in edge_rows:
            a_name = row["a_name"]
            b_name = row["b_name"]
            if a_name is None or b_name is None:
                continue
            self._add_edge(graph, str(a_name), str(b_name), 1.0)

        for connector_rows in self._vertical_connector_groups().values():
            previous_name: str | None = None
            previous_floor: int | None = None
            for floor, node_name in connector_rows:
                if previous_name is not None and previous_floor is not None:
                    self._add_edge(graph, previous_name, node_name, float(abs(floor - previous_floor)))
                previous_name = node_name
                previous_floor = floor

        outside_rows = self._query_runner.run(
            """
            MATCH (d:Door {is_outside: true})-[:CONNECTS_TO]-(r:Room)
            RETURN DISTINCT coalesce(r.name, toString(r.id)) AS room_name
            """,
        )
        if outside_rows:
            graph["outside"]
            self._add_lookup(lookup, "outside", "outside")
            for row in outside_rows:
                room_name = row["room_name"]
                if room_name is None:
                    continue
                self._add_edge(graph, "outside", str(room_name), 1.0)

        return StateGraph(adjacency=dict(graph), lookup=lookup)

    def load_map_rects(self) -> Dict[int, List[SpaceRect]]:
        rows = self._query_runner.run(
            """
            MATCH (g:Room)
            RETURN
              coalesce(g.name, toString(g.id)) AS name,
              coalesce(g.kind, "room") AS kind,
              toFloat(g.x) AS x,
              toFloat(g.y) AS y,
              toFloat(g.width) AS width,
              toFloat(g.height) AS height,
              toInteger(g.floor) AS floor
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
        rows = self._query_runner.run(
            """
            MATCH (r:Room)
            RETURN
              coalesce(r.name, toString(r.id)) AS node_key,
              toFloat(r.x) AS x,
              toFloat(r.y) AS y,
              toFloat(r.width) AS width,
              toFloat(r.height) AS height,
              toInteger(r.floor) AS floor
            """,
        )
        result: Dict[str, StatePosition] = {}
        for row in rows:
            key = row["node_key"]
            if key is None:
                continue
            x = float(row["x"] or 0.0)
            y = float(row["y"] or 0.0)
            width = float(row["width"] or 0.0)
            height = float(row["height"] or 0.0)
            floor = row["floor"]
            result[str(key)] = StatePosition(
                x=x + (width / 2.0),
                y=y + (height / 2.0),
                floor=int(floor) if floor is not None else None,
            )
        if "outside" not in result:
            result["outside"] = StatePosition(x=0.0, y=0.0, floor=None)
        return result

    @staticmethod
    def _add_edge(graph: Dict[str, List[Tuple[str, float]]], a_name: str, b_name: str, cost: float) -> None:
        graph[a_name].append((b_name, cost))
        graph[b_name].append((a_name, cost))

    def _has_outside_room_connection(self) -> bool:
        rows = self._query_runner.run(
            """
            MATCH (:Door {is_outside: true})-[:CONNECTS_TO]-(:Room)
            RETURN 1 AS found
            LIMIT 1
            """,
        )
        return bool(rows)

    def _vertical_connector_groups(self) -> Dict[str, List[Tuple[int, str]]]:
        rows = self._query_runner.run(
            """
            MATCH (r:Room)
            WHERE toLower(trim(coalesce(r.kind, ""))) IN ["elevator", "escalator"]
            RETURN
              coalesce(r.name, toString(r.id)) AS node_name,
              coalesce(r.name, toString(r.id)) AS raw_name,
              toInteger(r.floor) AS floor
            """,
        )

        grouped: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
        for row in rows:
            node_name = row["node_name"]
            raw_name = row["raw_name"]
            floor = row["floor"]
            if node_name is None or raw_name is None or floor is None:
                continue
            connector_key = re.sub(r"_f\d+$", "", str(raw_name))
            grouped[connector_key].append((int(floor), str(node_name)))

        return {
            key: sorted(values, key=lambda item: item[0])
            for key, values in grouped.items()
        }
