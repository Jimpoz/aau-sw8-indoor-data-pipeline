"""Payload summaries and import helpers for the local floor data program."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_workspace import relative_path
import floor_import_builder


def summarize_details(details: dict[str, Any], details_path: Path) -> dict[str, Any]:
    rooms = details.get("rooms") or []
    doors = details.get("doors") or []
    diagnostics = details.get("cv_diagnostics") or {}
    return {
        "path": relative_path(details_path),
        "building": details.get("building"),
        "floor": details.get("floor"),
        "floor_index": details.get("floor_index"),
        "rooms": len(rooms),
        "doors": len(doors),
        "ocr_words_seen": diagnostics.get("ocr_words_seen"),
        "room_crop_names_seen": diagnostics.get("room_crop_names_seen"),
        "door_labels_seen": diagnostics.get("door_labels_seen"),
        "warnings": diagnostics.get("warnings", [])[:30],
    }


def summarize_import_payload(import_json: dict[str, Any], import_path: Path) -> dict[str, Any]:
    campus = import_json.get("campus") or {}
    building = (campus.get("buildings") or [{}])[0]
    floor = (building.get("floors") or [{}])[0]
    spaces = floor.get("spaces") or []
    connections = campus.get("connections") or []
    return {
        "path": relative_path(import_path),
        "organization": (import_json.get("organization") or {}).get("name"),
        "campus": campus.get("name"),
        "building": building.get("name"),
        "floor": floor.get("display_name"),
        "spaces": len(spaces),
        "rooms": sum(1 for space in spaces if not str(space.get("space_type", "")).startswith("DOOR")),
        "door_spaces": sum(1 for space in spaces if str(space.get("space_type", "")).startswith("DOOR")),
        "connections": len(connections),
    }


def make_import_payload(
    *,
    details_path: Path,
    export: dict[str, Any] | None,
    target: dict[str, Any],
    include_rooms: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    floor_payload = floor_import_builder.build_floor_payload_from_details(details_path)
    resolved = floor_import_builder.resolve_target(
        export,
        organization_id=target.get("organization_id") or "org-aau",
        organization_name=target.get("organization_name") or "Aalborg University",
        campus_id=target.get("campus_id") or "campus-aau-cph",
        campus_name=target.get("campus_name") or "AAU CPH",
        building_id=target.get("building_id") or None,
        building_name=target.get("building_name") or "Test",
        floor_id=target.get("floor_id") or None,
        floor_name=target.get("floor_name") or "Test",
        floor_index=int(target.get("floor_index") or 0),
    )
    return floor_import_builder.build_import_json(
        floor_payload,
        resolved,
        include_rooms=include_rooms,
    )


def split_import_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rooms_payload = json.loads(json.dumps(payload))
    connections_payload = json.loads(json.dumps(payload))

    rooms_payload["campus"]["connections"] = []
    for building in rooms_payload["campus"].get("buildings", []):
        for floor in building.get("floors", []):
            floor["spaces"] = [
                space
                for space in floor.get("spaces", [])
                if not str(space.get("space_type", "")).startswith("DOOR")
            ]

    connections_payload["campus"]["outdoor_spaces"] = []
    for building in connections_payload["campus"].get("buildings", []):
        for floor in building.get("floors", []):
            floor["spaces"] = [
                space
                for space in floor.get("spaces", [])
                if str(space.get("space_type", "")).startswith("DOOR")
            ]

    return rooms_payload, connections_payload
