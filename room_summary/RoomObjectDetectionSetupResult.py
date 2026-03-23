from __future__ import annotations

from dataclasses import dataclass

from .ViewSummary import ViewSummary


@dataclass(slots=True)
class RoomObjectDetectionSetupResult:
    room_name: str
    room_objects: list[str]
    room_object_counts_json: str
    room_summary: list[ViewSummary]

    def to_dict(self) -> dict[str, object]:
        return {
            "room_name": self.room_name,
            "roomObjects": self.room_objects,
            "roomObjectCountsJson": self.room_object_counts_json,
            "room_summary": [view.to_summary_dict() for view in self.room_summary],
        }
