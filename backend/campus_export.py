"""Objects for selecting a floor from a Map API campus export."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import floor_import_builder


def safe_file_name(value: str) -> str:
    return floor_import_builder.stable_id(value).replace("-", "_")


def summarize_export(export: dict[str, Any]) -> dict[str, Any]:
    organization = export.get("organization") or {}
    campus = export.get("campus") or {}
    connections = campus.get("connections") or []
    buildings = []
    for building in campus.get("buildings", []):
        floors = []
        for floor in building.get("floors", []):
            spaces = floor.get("spaces") or []
            floors.append(
                {
                    "id": floor.get("id"),
                    "display_name": floor.get("display_name"),
                    "floor_index": floor.get("floor_index"),
                    "spaces_count": len(spaces),
                    "room_spaces_count": sum(
                        1 for space in spaces if not str(space.get("space_type", "")).startswith("DOOR")
                    ),
                    "door_spaces_count": sum(
                        1 for space in spaces if str(space.get("space_type", "")).startswith("DOOR")
                    ),
                }
            )
        buildings.append(
            {
                "id": building.get("id"),
                "name": building.get("name"),
                "floors": floors,
            }
        )
    return {
        "organization": organization,
        "campus": {
            "id": campus.get("id"),
            "name": campus.get("name"),
            "organization_id": campus.get("organization_id"),
        },
        "connections_count": len(connections),
        "buildings": buildings,
    }


@dataclass(frozen=True)
class FloorSelection:
    campus: dict[str, Any]
    building: dict[str, Any]
    floor: dict[str, Any]
    connections: list[dict[str, Any]]

    @property
    def spaces(self) -> list[dict[str, Any]]:
        return list(self.floor.get("spaces") or [])

    def fallback_filename(self, output_format: str) -> str:
        return (
            f"{safe_file_name(str(self.building.get('name') or self.building.get('id') or 'building'))}_"
            f"{safe_file_name(str(self.floor.get('display_name') or self.floor.get('id') or 'floor'))}_"
            f"floor_plan.{output_format}"
        )


@dataclass(frozen=True)
class MapExportSnapshot:
    export: dict[str, Any]

    @property
    def campus(self) -> dict[str, Any]:
        return self.export.get("campus") or {}

    def summary(self) -> dict[str, Any]:
        return summarize_export(self.export)

    def select_floor(self, *, building_id: str, floor_id: str) -> FloorSelection:
        building = next(
            (item for item in self.campus.get("buildings", []) if str(item.get("id")) == str(building_id)),
            None,
        )
        if not building:
            raise ValueError("Building was not found in the Map API export")
        floor = next(
            (item for item in building.get("floors", []) if str(item.get("id")) == str(floor_id)),
            None,
        )
        if not floor:
            raise ValueError("Floor was not found in the Map API export")

        spaces = list(floor.get("spaces") or [])
        space_ids = {space.get("id") for space in spaces}
        connections = [
            conn
            for conn in self.campus.get("connections", [])
            if conn.get("from_space_id") in space_ids or conn.get("to_space_id") in space_ids
        ]
        return FloorSelection(
            campus=self.campus,
            building=building,
            floor=floor,
            connections=connections,
        )


def find_export_floor(
    export: dict[str, Any],
    *,
    building_id: str,
    floor_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    selection = MapExportSnapshot(export).select_floor(
        building_id=building_id,
        floor_id=floor_id,
    )
    return selection.campus, selection.building, selection.floor, selection.connections
