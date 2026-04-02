from __future__ import annotations

from ..configuration.TranslatorConfig import TranslatorConfig
from ..results.PageAnalysis import PageAnalysis


class MapImportBuilder:
    def __init__(self, config: TranslatorConfig) -> None:
        self.config = config

    def build(
        self,
        campus_id: str,
        campus_name: str,
        building_id: str,
        building_name: str,
        floors: list[PageAnalysis],
    ) -> dict[str, object]:
        building_floors: list[dict[str, object]] = []
        connections: list[dict[str, object]] = []
        for floor in floors:
            building_floors.append(
                {
                    "id": floor.id,
                    "floor_index": floor.floor_index,
                    "display_name": floor.display_name,
                    "elevation_m": None,
                    "floor_plan_url": None,
                    "floor_plan_scale": floor.scale_meters_per_pixel,
                    "floor_plan_origin_x": 0.0,
                    "floor_plan_origin_y": 0.0,
                    "spaces": [space.to_map_import_dict() for space in floor.spaces],
                }
            )
            connections.extend(door.to_map_import_dict() for door in floor.doors)

        return {
            "schema_version": self.config.export.schema_version,
            "campus": {
                "id": campus_id,
                "name": campus_name,
                "description": "Imported from floor-plan translator",
                "buildings": [
                    {
                        "id": building_id,
                        "name": building_name,
                        "short_name": self.config.project.default_building_short_name,
                        "address": None,
                        "origin_lat": None,
                        "origin_lng": None,
                        "origin_bearing": 0.0,
                        "floor_count": len(building_floors),
                        "floors": building_floors,
                    }
                ],
                "outdoor_spaces": [],
                "connections": connections,
            },
        }
