#!/usr/bin/env python
"""Build Map API floor import JSON from extracted floor details."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from floor_plan_details_reader import read_floor_payload, read_lookup


DEFAULT_API_EXPORT = (
    "https://retaliatory-bruna-unofficious.ngrok-free.dev"
    "/api/v1/campuses/campus-aau-cph/export"
)
DOOR_IMPORT_SHAPE = "edge-metadata"


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def stable_id(*parts: str) -> str:
    text = "-".join(part for part in parts if part)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "generated-id"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class FloorDetailsPayloadBuilder:
    details_path: Path

    def build(self) -> dict[str, Any]:
        details = read_lookup(self.details_path)
        rooms = details["rooms"]
        doors = details["doors"]
        building_name = str(details.get("building", ""))
        floor_name = str(details.get("floor", details.get("floor_index", "")))
        floor_index = int(details.get("floor_index", 0) or 0)
        return {
            "schema": "aau_map_upload_payload",
            "version": 1,
            "source_files": {
                "pdf": "none:image-derived-details",
                "details": str(self.details_path),
            },
            "pdf": {
                "info": {},
                "metadata": {
                    "schema": "aau_map_image_details",
                    "version": 1,
                    "building_name": building_name,
                    "floor_index": floor_index,
                    "floor_name": floor_name,
                    "coordinate_unit": "meter",
                    "spaces_count": len(rooms),
                    "doors_count": len(doors),
                    "scale_pdf_points_per_meter": None,
                    "meter_per_pdf_point": None,
                },
                "scale": {},
            },
            "rooms": rooms,
            "doors": doors,
            "validation": {
                "ok": True,
                "warnings": [],
                "rooms_in_details": len(rooms),
                "doors_in_details": len(doors),
                "room_ids_seen_in_pdf": 0,
                "door_ids_seen_in_pdf": 0,
            },
        }


def build_floor_payload_from_details(details_path: Path) -> dict[str, Any]:
    return FloorDetailsPayloadBuilder(details_path).build()


@dataclass(frozen=True)
class CampusExportLoader:
    path: Path | None = None
    api_url: str | None = None

    def load(self) -> dict[str, Any] | None:
        if self.path:
            return load_json(self.path)
        if not self.api_url:
            return None
        request = Request(self.api_url, headers={"ngrok-skip-browser-warning": "true"})
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))


def load_export(path: Path | None, api_url: str | None) -> dict[str, Any] | None:
    return CampusExportLoader(path, api_url).load()


def find_by_id_or_name(
    items: list[dict[str, Any]],
    *,
    wanted_id: str | None,
    wanted_name: str | None,
    name_key: str = "name",
) -> dict[str, Any] | None:
    if wanted_id:
        for item in items:
            if str(item.get("id", "")) == wanted_id:
                return item
    if wanted_name:
        wanted = normalized(wanted_name)
        for item in items:
            if normalized(str(item.get(name_key, ""))) == wanted:
                return item
    return None


@dataclass(frozen=True)
class TargetSpec:
    organization_id: str
    organization_name: str
    campus_id: str
    campus_name: str
    building_id: str | None
    building_name: str
    floor_id: str | None
    floor_name: str
    floor_index: int


@dataclass(frozen=True)
class TargetResolver:
    export: dict[str, Any] | None = None

    def resolve(self, spec: TargetSpec) -> dict[str, Any]:
        target = self._default_target(spec)
        if not self.export:
            return target

        organization = self.export.get("organization") or {}
        campus = self.export.get("campus") or {}
        if organization:
            target["organization"] = {
                "id": str(organization.get("id", spec.organization_id)),
                "name": str(organization.get("name", spec.organization_name)),
                "entity_type": str(organization.get("entity_type", "UNIVERSITY")),
            }
        if campus:
            target["campus"] = {
                "id": str(campus.get("id", spec.campus_id)),
                "name": str(campus.get("name", spec.campus_name)),
                "organization_id": str(campus.get("organization_id", target["organization"]["id"])),
            }
        self._resolve_building_and_floor(target, campus, spec)
        return target

    def _default_target(self, spec: TargetSpec) -> dict[str, Any]:
        return {
            "organization": {
                "id": spec.organization_id,
                "name": spec.organization_name,
                "entity_type": "UNIVERSITY",
            },
            "campus": {
                "id": spec.campus_id,
                "name": spec.campus_name,
                "organization_id": spec.organization_id,
            },
            "building": {
                "id": spec.building_id or stable_id(spec.campus_id, spec.building_name),
                "name": spec.building_name,
                "organization_id": spec.organization_id,
            },
            "floor": {
                "id": spec.floor_id or stable_id(spec.campus_id, spec.building_name, spec.floor_name),
                "floor_index": spec.floor_index,
                "display_name": spec.floor_name,
            },
            "target_floor_existing_spaces": None,
        }

    def _resolve_building_and_floor(
        self,
        target: dict[str, Any],
        campus: dict[str, Any],
        spec: TargetSpec,
    ) -> None:
        building = find_by_id_or_name(
            list(campus.get("buildings", [])),
            wanted_id=spec.building_id,
            wanted_name=spec.building_name,
        )
        if not building:
            return

        target["building"] = {
            "id": str(building.get("id", target["building"]["id"])),
            "name": str(building.get("name", spec.building_name)),
            "organization_id": str(building.get("organization_id", target["organization"]["id"])),
        }
        floor = find_by_id_or_name(
            list(building.get("floors", [])),
            wanted_id=spec.floor_id,
            wanted_name=spec.floor_name,
            name_key="display_name",
        )
        if not floor:
            return

        target["floor"] = {
            "id": str(floor.get("id", target["floor"]["id"])),
            "floor_index": int(floor.get("floor_index", spec.floor_index)),
            "display_name": str(floor.get("display_name", spec.floor_name)),
        }
        target["target_floor_existing_spaces"] = len(floor.get("spaces", []))


def resolve_target(
    export: dict[str, Any] | None,
    *,
    organization_id: str,
    organization_name: str,
    campus_id: str,
    campus_name: str,
    building_id: str | None,
    building_name: str,
    floor_id: str | None,
    floor_name: str,
    floor_index: int,
) -> dict[str, Any]:
    return TargetResolver(export).resolve(
        TargetSpec(
            organization_id=organization_id,
            organization_name=organization_name,
            campus_id=campus_id,
            campus_name=campus_name,
            building_id=building_id,
            building_name=building_name,
            floor_id=floor_id,
            floor_name=floor_name,
            floor_index=floor_index,
        )
    )


def require_room_polygons(rooms: list[dict[str, Any]]) -> None:
    missing = [room["unique_id"] for room in rooms if not room.get("polygon_m")]
    if missing:
        raise ValueError(
            "Details JSON is missing polygon_m for rooms. Run extraction again "
            f"to regenerate it. First missing IDs: {', '.join(missing[:12])}"
        )


@dataclass(frozen=True)
class RoomSpaceTranslator:
    rooms: list[dict[str, Any]]
    target: dict[str, Any]

    def translate(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        spaces: list[dict[str, Any]] = []
        room_id_map: dict[str, str] = {}
        for room in self.rooms:
            unique_id = str(room["unique_id"])
            server_id = stable_id(self.floor["id"], unique_id)
            room_id_map[unique_id] = server_id
            spaces.append(self._space_for_room(room, server_id, unique_id))
        return spaces, room_id_map

    @property
    def floor(self) -> dict[str, Any]:
        return self.target["floor"]

    @property
    def building(self) -> dict[str, Any]:
        return self.target["building"]

    @property
    def campus(self) -> dict[str, Any]:
        return self.target["campus"]

    @property
    def organization(self) -> dict[str, Any]:
        return self.target["organization"]

    def _space_for_room(
        self,
        room: dict[str, Any],
        server_id: str,
        unique_id: str,
    ) -> dict[str, Any]:
        return {
            "id": server_id,
            "display_name": str(room.get("name") or unique_id),
            "space_type": str(room.get("type") or "ROOM_GENERIC"),
            "organization_id": self.organization["id"],
            "campus_id": self.campus["id"],
            "building_id": self.building["id"],
            "floor_id": self.floor["id"],
            "floor_index": self.floor["floor_index"],
            "centroid_x": room.get("centroid_m", {}).get("x"),
            "centroid_y": room.get("centroid_m", {}).get("y"),
            "polygon": room["polygon_m"],
            "area_m2": room.get("area_m2"),
            "is_accessible": True,
            "is_navigable": True,
            "is_outdoor": False,
            "tags": [
                f"aau_pdf_room_id:{unique_id}",
                f"aau_source_room_id:{room.get('source_id', '')}",
            ],
        }


def translate_spaces(
    rooms: list[dict[str, Any]],
    target: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    return RoomSpaceTranslator(rooms, target).translate()


@dataclass(frozen=True)
class DoorConnectionTranslator:
    doors: list[dict[str, Any]]
    target: dict[str, Any]
    room_id_map: dict[str, str]

    def translate(self) -> tuple[list[dict[str, Any]], list[str]]:
        connections: list[dict[str, Any]] = []
        warnings: list[str] = []

        for door in self.doors:
            translated = self.connected_room_ids(door)
            if len(translated) < 2:
                warnings.append(f"Skipped {door.get('unique_id')}: less than two translated rooms")
                continue

            base = self._connection_base(door)
            first, second = translated[0], translated[1]
            connections.append({**base, "from_space_id": first, "to_space_id": second})
            connections.append({**base, "from_space_id": second, "to_space_id": first})

        return connections, warnings

    def translated_room_id(self, room_ref: dict[str, Any]) -> str | None:
        unique_id = str(room_ref.get("unique_id", ""))
        return self.room_id_map.get(unique_id)

    def connected_room_ids(self, door: dict[str, Any]) -> list[str]:
        translated: list[str] = []
        for room_ref in list(door.get("connected_rooms", []))[:2]:
            room_id = self.translated_room_id(room_ref)
            if room_id:
                translated.append(room_id)
        return translated

    def door_node_id(self, door: dict[str, Any]) -> str:
        return stable_id(str(self.target["floor"]["id"]), str(door["unique_id"]))

    def _connection_base(self, door: dict[str, Any]) -> dict[str, Any]:
        centroid = door.get("centroid_m", {})
        return {
            "connection_type": "DOOR",
            "is_accessible": True,
            "requires_access_level": None,
            "transition_time_s": None,
            "weight_override": None,
            "door_id": self.door_node_id(door),
            "door_cx": centroid.get("x"),
            "door_cy": centroid.get("y"),
            "door_type": "STANDARD",
        }


def translated_room_id(
    room_ref: dict[str, Any],
    room_id_map: dict[str, str],
) -> str | None:
    return DoorConnectionTranslator([], {}, room_id_map).translated_room_id(room_ref)


def connected_room_ids_for_door(
    door: dict[str, Any],
    room_id_map: dict[str, str],
) -> list[str]:
    return DoorConnectionTranslator([], {}, room_id_map).connected_room_ids(door)


def door_node_id(door: dict[str, Any], target: dict[str, Any]) -> str:
    return DoorConnectionTranslator([], target, {}).door_node_id(door)


def translate_connections(
    doors: list[dict[str, Any]],
    target: dict[str, Any],
    room_id_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    return DoorConnectionTranslator(doors, target, room_id_map).translate()


@dataclass(frozen=True)
class FloorImportBuilder:
    floor_payload: dict[str, Any]
    target: dict[str, Any]
    include_rooms: bool = True

    def build(self) -> tuple[dict[str, Any], dict[str, Any]]:
        rooms = self.floor_payload["rooms"]
        doors = self.floor_payload["doors"]
        require_room_polygons(rooms)

        room_spaces, room_id_map = RoomSpaceTranslator(rooms, self.target).translate()
        spaces = room_spaces if self.include_rooms else []
        connections, connection_warnings = DoorConnectionTranslator(doors, self.target, room_id_map).translate()

        import_json = self._build_import_json(spaces, connections)
        summary = self._build_summary(
            room_spaces=room_spaces,
            spaces=spaces,
            doors=doors,
            connections=connections,
            warnings=connection_warnings,
        )
        return import_json, summary

    def _build_import_json(
        self,
        spaces: list[dict[str, Any]],
        connections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "organization": self.target["organization"],
            "campus": {
                **self.target["campus"],
                "buildings": [
                    {
                        **self.target["building"],
                        "floors": [
                            {
                                **self.target["floor"],
                                "floor_plan_scale": 1.0,
                                "floor_plan_origin_x": 0.0,
                                "floor_plan_origin_y": 0.0,
                                "spaces": spaces,
                            }
                        ],
                    }
                ],
                "outdoor_spaces": [],
                "connections": connections,
            },
        }

    def _build_summary(
        self,
        *,
        room_spaces: list[dict[str, Any]],
        spaces: list[dict[str, Any]],
        doors: list[dict[str, Any]],
        connections: list[dict[str, Any]],
        warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "target": {
                "campus": self.target["campus"],
                "building": self.target["building"],
                "floor": self.target["floor"],
                "target_floor_existing_spaces": self.target["target_floor_existing_spaces"],
            },
            "source": {
                "rooms": len(self.floor_payload["rooms"]),
                "doors": len(doors),
                "scale": self.floor_payload["pdf"]["scale"],
            },
            "translated": {
                "room_spaces": len(room_spaces) if self.include_rooms else 0,
                "door_spaces": 0,
                "spaces": len(spaces),
                "doors": len(doors),
                "door_connections": len(connections),
                "door_ids": len(doors),
                "door_shape": DOOR_IMPORT_SHAPE,
                "include_rooms": self.include_rooms,
            },
            "warnings": warnings,
        }


def build_import_json(
    floor_payload: dict[str, Any],
    target: dict[str, Any],
    *,
    include_rooms: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return FloorImportBuilder(floor_payload, target, include_rooms).build()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, help="Generated map PDF with embedded details.")
    parser.add_argument(
        "--details",
        type=Path,
        help="Optional CSV/JSON details sidecar. Defaults to embedded PDF text.",
    )
    parser.add_argument(
        "--details-only",
        action="store_true",
        help="Translate details JSON/CSV directly, without reading a generated PDF.",
    )
    parser.add_argument("--campus-export", type=Path)
    parser.add_argument(
        "--api-url",
        default=None,
        help="Optional read-only export URL used to resolve target IDs.",
    )
    parser.add_argument("--organization-id", default="org-aau")
    parser.add_argument("--organization-name", default="Aalborg University")
    parser.add_argument("--campus-id", default="campus-aau-cph")
    parser.add_argument("--campus-name", default="AAU CPH")
    parser.add_argument("--building-id")
    parser.add_argument("--building-name", default="Test")
    parser.add_argument("--floor-id")
    parser.add_argument("--floor-name", default="Test")
    parser.add_argument("--floor-index", type=int, default=0)
    parser.add_argument(
        "--only-doors",
        action="store_true",
        help="Write only door connections. Use this when the rooms already exist.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write the mapmaker import JSON. Use '-' for stdout.",
    )
    args = parser.parse_args()

    if args.details_only:
        if not args.details:
            parser.error("--details-only requires --details")
        floor_payload = build_floor_payload_from_details(args.details)
    else:
        if not args.pdf:
            parser.error("provide --details-only --details, or provide --pdf")
        floor_payload = read_floor_payload(args.pdf, args.details)

    if not floor_payload["validation"]["ok"]:
        warnings = "\n".join(floor_payload["validation"]["warnings"])
        raise ValueError(f"Floor file validation failed:\n{warnings}")

    export = load_export(args.campus_export, args.api_url)
    target = resolve_target(
        export,
        organization_id=args.organization_id,
        organization_name=args.organization_name,
        campus_id=args.campus_id,
        campus_name=args.campus_name,
        building_id=args.building_id,
        building_name=args.building_name,
        floor_id=args.floor_id,
        floor_name=args.floor_name,
        floor_index=args.floor_index,
    )
    import_json, summary = build_import_json(
        floor_payload,
        target,
        include_rooms=not args.only_doors,
    )

    if args.output:
        text = json.dumps(import_json, ensure_ascii=False, indent=2, sort_keys=True)
        if str(args.output) == "-":
            print(text)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")

    print(f"Target campus: {target['campus']['name']} ({target['campus']['id']})")
    print(f"Target building: {target['building']['name']} ({target['building']['id']})")
    print(f"Target floor: {target['floor']['display_name']} ({target['floor']['id']})")
    if target["target_floor_existing_spaces"] is not None:
        print(f"Target floor currently has {target['target_floor_existing_spaces']} spaces")
    print(
        "Translated: "
        f"{summary['translated']['room_spaces']} room spaces, "
        f"{summary['translated']['door_spaces']} door spaces, "
        f"{summary['translated']['doors']} doors, "
        f"{summary['translated']['door_connections']} directed door connections"
    )
    print(f"Door shape: {summary['translated']['door_shape']}")
    if args.only_doors:
        print("Scope: doors only")
    if summary["warnings"]:
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"- {warning}")
    else:
        print("Translation: OK")
    if args.output and str(args.output) != "-":
        print(args.output)


if __name__ == "__main__":
    main()
