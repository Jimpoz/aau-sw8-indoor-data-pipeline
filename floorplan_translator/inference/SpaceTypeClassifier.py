from __future__ import annotations

from ..configuration.TranslatorConfig import TranslatorConfig


SPACE_TYPE_KEYWORDS = {
    "wc": "RESTROOM",
    "toilet": "RESTROOM",
    "restroom": "RESTROOM",
    "bathroom": "RESTROOM",
    "gang": "CORRIDOR",
    "corridor": "CORRIDOR",
    "hallway": "CORRIDOR",
    "hall": "CORRIDOR",
    "lobby": "LOBBY",
    "entry": "ENTRANCE",
    "entrance": "ENTRANCE",
    "exit": "EXIT_EMERGENCY",
    "stair": "STAIRCASE",
    "trappe": "STAIRCASE",
    "elevator": "ELEVATOR",
    "lift": "ELEVATOR",
    "kitchen": "ROOM_GENERIC",
    "office": "ROOM_OFFICE",
    "seminar": "ROOM_CLASSROOM",
    "class": "ROOM_CLASSROOM",
    "laboratorium": "ROOM_LAB",
    "lab": "ROOM_LAB",
    "storage": "ROOM_STORAGE",
    "utility": "ROOM_UTILITY",
}


class SpaceTypeClassifier:
    def __init__(self, config: TranslatorConfig) -> None:
        self.config = config

    def classify(self, label: str | None, door_count: int, area_ratio: float) -> str:
        if label:
            normalized = label.lower()
            for keyword, space_type in SPACE_TYPE_KEYWORDS.items():
                if keyword in normalized:
                    return space_type
        if (
            door_count >= self.config.geometry.corridor_min_door_count
            and area_ratio >= self.config.geometry.corridor_min_area_ratio
        ):
            return self.config.project.default_corridor_type
        return self.config.project.default_room_type
