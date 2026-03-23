from __future__ import annotations

from pathfinding.query_runner import Neo4jQueryRunner


class RoomSummaryRepository:
    def __init__(self, query_runner: Neo4jQueryRunner) -> None:
        self._query_runner = query_runner

    @staticmethod
    def _normalize(text: str) -> str:
        return text.strip().lower()

    def list_room_names(self) -> list[str]:
        rows = self._query_runner.run(
            """
            MATCH (room:Room)
            RETURN coalesce(room.name, toString(room.id)) AS name
            ORDER BY name
            """,
        )
        return sorted({str(row["name"]) for row in rows if row["name"] is not None})

    def replace_room_detection_setup(
        self,
        room_name: str,
        room_objects: list[str],
        room_object_counts_json: str,
        room_images: list[str],
    ) -> str:
        rows = self._query_runner.run(
            """
            MATCH (room:Room)
            WHERE
              toLower(trim(coalesce(room.name, ""))) = $normalized_room_name
              OR toLower(trim(coalesce(toString(room.id), ""))) = $normalized_room_name
            WITH room
            LIMIT 1
            REMOVE room.roomObjects, room.roomObjectCountsJson, room.roomImages, room.roomSummaryUpdatedAt
            SET room.roomObjects = $room_objects,
                room.roomObjectCountsJson = $room_object_counts_json,
                room.roomImages = $room_images,
                room.roomSummaryUpdatedAt = datetime()
            RETURN coalesce(room.name, toString(room.id)) AS room_name
            """,
            normalized_room_name=self._normalize(room_name),
            room_objects=room_objects,
            room_object_counts_json=room_object_counts_json,
            room_images=room_images,
        )

        if not rows:
            raise LookupError(f"Room {room_name!r} was not found in Neo4j.")

        stored_room_name = rows[0]["room_name"]
        return str(stored_room_name or room_name)
