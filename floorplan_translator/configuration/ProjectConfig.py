from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ProjectConfig:
    default_building_name: str = "Imported Building"
    default_building_short_name: str = "imported"
    default_space_name_prefix: str = "Space"
    default_room_type: str = "ROOM_GENERIC"
    default_corridor_type: str = "CORRIDOR"
    default_door_type: str = "DOOR_STANDARD"
    backend_import_base_url: str | None = None
