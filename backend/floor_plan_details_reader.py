#!/usr/bin/env python
"""Read a generated AAU map PDF plus its JSON or CSV lookup file.

This script does not upload anything. It builds and validates the local payload
that an uploader can send later.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote


PDF_METADATA_PREFIX = "AAU_MAP_METADATA_JSON="
SCALE_BLOCK_BEGIN = "AAU_SCALE_BLOCK_BEGIN"
SCALE_BLOCK_END = "AAU_SCALE_BLOCK_END"
PDF_DETAILS_SCHEMA_V1_LINE = "AAU_DETAILS_SCHEMA=aau_map_text_v1"
PDF_DETAILS_SCHEMA_V2_LINE = "AAU_DETAILS_SCHEMA=aau_map_text_v2"
PDF_DETAILS_SCHEMA_LINE = PDF_DETAILS_SCHEMA_V2_LINE
PDF_DETAILS_ROW_RE = re.compile(r"AAU_(?:ROOM|DOOR);[^\s]+")
PDF_DETAILS_JSON_BEGIN = b"%AAU_DETAILS_JSON_BEGIN"
PDF_DETAILS_JSON_END = b"%AAU_DETAILS_JSON_END"


def run_text_command(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


@dataclass(frozen=True)
class PdfDocument:
    path: Path

    def text(self) -> str:
        layout = run_text_command(["pdftotext", "-layout", str(self.path), "-"])
        raw = run_text_command(["pdftotext", "-raw", str(self.path), "-"])
        return layout if raw in layout else f"{layout}\n{raw}"

    def info(self) -> dict[str, str]:
        info: dict[str, str] = {}
        for line in run_text_command(["pdfinfo", str(self.path)]).splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
        return info

    def metadata(self) -> dict[str, Any]:
        return extract_pdf_metadata(self.text())

    def scale(self) -> dict[str, str | int | float]:
        return extract_scale_block(self.text())

    def embedded_details(self) -> dict[str, Any] | None:
        return read_embedded_details_json(self.path)


def read_pdf_text(pdf_path: Path) -> str:
    return PdfDocument(pdf_path).text()


def read_pdf_info(pdf_path: Path) -> dict[str, str]:
    return PdfDocument(pdf_path).info()


def extract_pdf_metadata(pdf_text: str) -> dict[str, Any]:
    for line in pdf_text.splitlines():
        if PDF_METADATA_PREFIX not in line:
            continue
        metadata_text = line.split(PDF_METADATA_PREFIX, 1)[1].strip()
        try:
            return json.loads(metadata_text)
        except json.JSONDecodeError:
            break

    compact = {
        key: value
        for key, value in re.findall(r"(AAU_MAP_META_[A-Z_]+)=([^\s]+)", pdf_text)
    }
    if compact:
        return {
            "schema": "aau_map_pdf",
            "version": 1,
            "building_name": unquote(compact.get("AAU_MAP_META_BUILDING", "")),
            "floor_index": int(compact.get("AAU_MAP_META_FLOOR_INDEX", "0")),
            "floor_name": unquote(compact.get("AAU_MAP_META_FLOOR", "")),
            "coordinate_unit": "meter",
            "spaces_count": int(compact.get("AAU_MAP_META_SPACES_COUNT", "0")),
            "doors_count": int(compact.get("AAU_MAP_META_DOORS_COUNT", "0")),
        }
    raise ValueError(f"PDF does not contain {PDF_METADATA_PREFIX} metadata")


def parse_scale_value(value: str) -> str | int | float:
    value = value.strip()
    try:
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            return float(value)
    except ValueError:
        pass
    return value


def extract_scale_block(pdf_text: str) -> dict[str, str | int | float]:
    in_block = False
    scale: dict[str, str | int | float] = {}
    for raw_line in pdf_text.splitlines():
        line = raw_line.strip()
        if SCALE_BLOCK_BEGIN in line:
            in_block = True
            continue
        if SCALE_BLOCK_END in line:
            return scale
        if not in_block:
            continue
        match = re.search(
            r"(AAU_SCALE_[A-Z0-9_]+)=(-?\d+(?:\.\d+)?|[A-Za-z0-9_.-]+)",
            line,
        )
        if not match:
            continue
        key, value = match.groups()
        scale[key] = parse_scale_value(value)
    raise ValueError(f"PDF does not contain {SCALE_BLOCK_BEGIN}/{SCALE_BLOCK_END}")


def validate_lookup_data(details: dict[str, Any]) -> dict[str, Any]:
    if details.get("schema") != "aau_map_details":
        raise ValueError("Details JSON must use schema=aau_map_details")
    if not isinstance(details.get("rooms"), list) or not isinstance(details.get("doors"), list):
        raise ValueError("Details JSON must contain rooms and doors arrays")
    return details


def read_lookup_json(details_path: Path) -> dict[str, Any]:
    details = json.loads(details_path.read_text(encoding="utf-8"))
    return validate_lookup_data(details)


def read_embedded_details_json(pdf_path: Path) -> dict[str, Any] | None:
    data = pdf_path.read_bytes()
    start = data.find(PDF_DETAILS_JSON_BEGIN)
    if start < 0:
        return None
    start = data.find(b"\n", start)
    if start < 0:
        return None
    start += 1
    end = data.find(PDF_DETAILS_JSON_END, start)
    if end < 0:
        return None
    encoded = b"".join(
        line.strip().lstrip(b"%")
        for line in data[start:end].splitlines()
        if line.strip()
    )
    if not encoded:
        return None
    details = json.loads(base64.b64decode(encoded).decode("utf-8"))
    return validate_lookup_data(details)


def split_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        item.strip()
        for item in re.split(r"[,|]", value)
        if item.strip()
    ]


def maybe_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def maybe_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def read_lookup_csv(details_path: Path) -> dict[str, Any]:
    rooms: list[dict[str, Any]] = []
    doors: list[dict[str, Any]] = []
    with details_path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        if not reader.fieldnames or "entity" not in reader.fieldnames:
            raise ValueError("Details CSV must contain an entity column")
        for row in reader:
            entity = (row.get("entity") or "").strip().lower()
            unique_id = (row.get("unique_id") or "").strip()
            if not entity or not unique_id:
                continue

            floor_index = maybe_int(row.get("floor_index")) or 0
            centroid = {
                "x": maybe_float(row.get("centroid_x_m")),
                "y": maybe_float(row.get("centroid_y_m")),
            }
            base = {
                "unique_id": unique_id,
                "source_id": row.get("source_id") or f"csv:{unique_id}",
                "name": row.get("name") or unique_id,
                "type": row.get("type") or ("DOOR" if entity == "door" else "ROOM_GENERIC"),
                "building": row.get("building") or "",
                "floor": row.get("floor") or "",
                "floor_index": floor_index,
                "centroid_m": centroid,
            }

            if entity == "room":
                polygon_text = row.get("polygon_m") or "[]"
                try:
                    polygon = json.loads(polygon_text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid polygon_m JSON for {unique_id}") from exc
                width = maybe_float(row.get("width_m"))
                height = maybe_float(row.get("height_m"))
                rooms.append(
                    {
                        **base,
                        "dimensions_m": {"width": width, "height": height},
                        "polygon_m": polygon,
                        "area_m2": maybe_float(row.get("area_m2")),
                        "door_count": maybe_int(row.get("door_count")) or len(split_ids(row.get("door_ids"))),
                        "doors": split_ids(row.get("door_ids")),
                    }
                )
            elif entity == "door":
                connected_room_ids = split_ids(row.get("connected_room_ids"))
                reference_room_id = (row.get("reference_room_id") or "").strip()
                doors.append(
                    {
                        **base,
                        "base_id": row.get("base_id") or unique_id[:-1],
                        "direction": row.get("direction") or unique_id[-1:],
                        "orientation": row.get("orientation") or "",
                        "size_m": maybe_float(row.get("size_m")),
                        "wall_length_m": maybe_float(row.get("wall_length_m")),
                        "reference_room": {
                            "unique_id": reference_room_id,
                            "source_id": f"csv:{reference_room_id}" if reference_room_id else "",
                            "name": reference_room_id,
                        },
                        "connected_rooms": [
                            {
                                "unique_id": room_id,
                                "source_id": f"csv:{room_id}",
                                "name": room_id,
                            }
                            for room_id in connected_room_ids
                        ],
                    }
                )
            else:
                raise ValueError(f"Unknown CSV entity for {unique_id}: {entity}")

    if not rooms or not doors:
        raise ValueError("Details CSV must contain room and door rows")

    first = rooms[0] if rooms else doors[0]
    return {
        "schema": "aau_map_details",
        "version": 1,
        "building": first.get("building", ""),
        "floor": first.get("floor", ""),
        "floor_index": first.get("floor_index", 0),
        "rooms": rooms,
        "doors": doors,
    }


@dataclass(frozen=True)
class DetailsLookupReader:
    details_path: Path

    def read(self) -> dict[str, Any]:
        if self.details_path.suffix.casefold() == ".csv":
            return self.read_csv()
        return self.read_json()

    def read_json(self) -> dict[str, Any]:
        return read_lookup_json(self.details_path)

    def read_csv(self) -> dict[str, Any]:
        return read_lookup_csv(self.details_path)


def read_lookup(details_path: Path) -> dict[str, Any]:
    return DetailsLookupReader(details_path).read()


def pdf_text_field(value: str) -> str:
    return unquote(value)


def parse_pdf_number(value: str) -> float | None:
    if value == "":
        return None
    return float(value)


def parse_pdf_int(value: str) -> int | None:
    if value == "":
        return None
    return int(float(value))


def read_lookup_from_pdf_text(
    pdf_text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        PDF_DETAILS_SCHEMA_V1_LINE not in pdf_text
        and PDF_DETAILS_SCHEMA_V2_LINE not in pdf_text
    ):
        raise ValueError(
            "PDF does not contain embedded AAU_DETAILS rows. "
            "Use the image/PDF extractor to generate a details JSON instead."
        )

    metadata = metadata or {}
    metadata_building = str(metadata.get("building_name", ""))
    metadata_floor = str(metadata.get("floor_name", metadata.get("floor_index", "")))
    metadata_floor_index = int(metadata.get("floor_index", 0) or 0)

    rooms: list[dict[str, Any]] = []
    doors: list[dict[str, Any]] = []
    for match in PDF_DETAILS_ROW_RE.finditer(pdf_text):
        fields = match.group(0).split(";")
        entity = fields[0]
        if entity == "AAU_ROOM":
            if len(fields) == 16:
                (
                    _,
                    unique_id,
                    source_id,
                    name,
                    room_type,
                    building,
                    floor,
                    floor_index,
                    centroid_x,
                    centroid_y,
                    width,
                    height,
                    area,
                    polygon,
                    door_count,
                    door_ids,
                ) = fields
                room_id = pdf_text_field(unique_id)
                rooms.append(
                    {
                        "unique_id": room_id,
                        "source_id": pdf_text_field(source_id),
                        "name": pdf_text_field(name),
                        "type": pdf_text_field(room_type),
                        "building": pdf_text_field(building),
                        "floor": pdf_text_field(floor),
                        "floor_index": parse_pdf_int(floor_index) or 0,
                        "centroid_m": {
                            "x": parse_pdf_number(centroid_x),
                            "y": parse_pdf_number(centroid_y),
                        },
                        "dimensions_m": {
                            "width": parse_pdf_number(width),
                            "height": parse_pdf_number(height),
                        },
                        "area_m2": parse_pdf_number(area),
                        "polygon_m": json.loads(pdf_text_field(polygon)),
                        "door_count": parse_pdf_int(door_count) or 0,
                        "doors": split_ids(pdf_text_field(door_ids)),
                    }
                )
                continue

            if len(fields) == 10:
                (
                    _,
                    unique_id,
                    name,
                    room_type,
                    centroid_x,
                    centroid_y,
                    width,
                    height,
                    area,
                    polygon,
                ) = fields
                room_id = pdf_text_field(unique_id)
                rooms.append(
                    {
                        "unique_id": room_id,
                        "source_id": f"pdf:{room_id}",
                        "name": pdf_text_field(name) or room_id,
                        "type": pdf_text_field(room_type) or "ROOM_GENERIC",
                        "building": metadata_building,
                        "floor": metadata_floor,
                        "floor_index": metadata_floor_index,
                        "centroid_m": {
                            "x": parse_pdf_number(centroid_x),
                            "y": parse_pdf_number(centroid_y),
                        },
                        "dimensions_m": {
                            "width": parse_pdf_number(width),
                            "height": parse_pdf_number(height),
                        },
                        "area_m2": parse_pdf_number(area),
                        "polygon_m": json.loads(pdf_text_field(polygon)),
                        "door_count": 0,
                        "doors": [],
                    }
                )
                continue

            raise ValueError(f"Bad embedded room row: {match.group(0)[:80]}")

        elif entity == "AAU_DOOR":
            if len(fields) == 17:
                (
                    _,
                    unique_id,
                    base_id,
                    source_id,
                    name,
                    door_type,
                    building,
                    floor,
                    floor_index,
                    centroid_x,
                    centroid_y,
                    direction,
                    orientation,
                    size,
                    wall_length,
                    reference_room_id,
                    connected_room_ids,
                ) = fields
                door_id = pdf_text_field(unique_id)
                reference_id = pdf_text_field(reference_room_id)
                connected_ids = split_ids(pdf_text_field(connected_room_ids))
                nearest_wall_distance = parse_pdf_number(wall_length)
                doors.append(
                    {
                        "unique_id": door_id,
                        "base_id": pdf_text_field(base_id),
                        "source_id": pdf_text_field(source_id),
                        "name": pdf_text_field(name),
                        "type": pdf_text_field(door_type),
                        "building": pdf_text_field(building),
                        "floor": pdf_text_field(floor),
                        "floor_index": parse_pdf_int(floor_index) or 0,
                        "centroid_m": {
                            "x": parse_pdf_number(centroid_x),
                            "y": parse_pdf_number(centroid_y),
                        },
                        "direction": pdf_text_field(direction),
                        "orientation": pdf_text_field(orientation),
                        "size_m": parse_pdf_number(size),
                        "wall_length_m": nearest_wall_distance,
                        "nearest_wall_distance_m": nearest_wall_distance,
                        "reference_room": {
                            "unique_id": reference_id,
                            "source_id": f"pdf:{reference_id}" if reference_id else "",
                            "name": reference_id,
                        },
                        "connected_rooms": [
                            {
                                "unique_id": room_id,
                                "source_id": f"pdf:{room_id}",
                                "name": room_id,
                            }
                            for room_id in connected_ids
                        ],
                    }
                )
                continue

            if len(fields) in {8, 9}:
                if len(fields) == 9:
                    (
                        _,
                        unique_id,
                        name,
                        centroid_x,
                        centroid_y,
                        direction,
                        orientation,
                        nearest_wall_distance,
                        connected_room_ids,
                    ) = fields
                else:
                    (
                        _,
                        unique_id,
                        centroid_x,
                        centroid_y,
                        direction,
                        orientation,
                        nearest_wall_distance,
                        connected_room_ids,
                    ) = fields
                    name = unique_id
                door_id = pdf_text_field(unique_id)
                direction_value = pdf_text_field(direction)
                connected_ids = split_ids(pdf_text_field(connected_room_ids))
                reference_id = connected_ids[0] if connected_ids else ""
                nearest_wall_distance_value = parse_pdf_number(nearest_wall_distance)
                base_id = (
                    door_id[: -len(direction_value)]
                    if direction_value and door_id.endswith(direction_value)
                    else door_id[:-1]
                )
                doors.append(
                    {
                        "unique_id": door_id,
                        "base_id": base_id,
                        "source_id": f"pdf:{door_id}",
                        "name": pdf_text_field(name) or door_id,
                        "type": "DOOR",
                        "building": metadata_building,
                        "floor": metadata_floor,
                        "floor_index": metadata_floor_index,
                        "centroid_m": {
                            "x": parse_pdf_number(centroid_x),
                            "y": parse_pdf_number(centroid_y),
                        },
                        "direction": direction_value,
                        "orientation": pdf_text_field(orientation),
                        "size_m": None,
                        "wall_length_m": nearest_wall_distance_value,
                        "nearest_wall_distance_m": nearest_wall_distance_value,
                        "reference_room": {
                            "unique_id": reference_id,
                            "source_id": f"pdf:{reference_id}" if reference_id else "",
                            "name": reference_id,
                        },
                        "connected_rooms": [
                            {
                                "unique_id": room_id,
                                "source_id": f"pdf:{room_id}",
                                "name": room_id,
                            }
                            for room_id in connected_ids
                        ],
                    }
                )
                continue

            raise ValueError(f"Bad embedded door row: {match.group(0)[:80]}")

    if not rooms or not doors:
        raise ValueError("Embedded PDF details block did not contain rooms and doors")

    rooms_by_id = {room["unique_id"]: room for room in rooms}
    for door in doors:
        for room_ref in door.get("connected_rooms", []):
            room = rooms_by_id.get(room_ref.get("unique_id"))
            if not room:
                continue
            room.setdefault("doors", [])
            if door["unique_id"] not in room["doors"]:
                room["doors"].append(door["unique_id"])
            room["door_count"] = len(room["doors"])

    first = rooms[0]
    return {
        "schema": "aau_map_details",
        "version": 1,
        "building": first.get("building", ""),
        "floor": first.get("floor", ""),
        "floor_index": first.get("floor_index", 0),
        "rooms": rooms,
        "doors": doors,
    }


def ids_seen_in_pdf(pdf_text: str, identifiers: list[str]) -> list[str]:
    return [
        identifier
        for identifier in identifiers
        if re.search(rf"(?<![A-Z0-9]){re.escape(identifier)}(?![A-Z0-9])", pdf_text)
    ]


@dataclass(frozen=True)
class FloorPlanReadbackValidator:
    pdf_text: str
    metadata: dict[str, Any]
    rooms: list[dict[str, Any]]
    doors: list[dict[str, Any]]
    require_text_identifiers: bool = True

    def validate(self) -> dict[str, Any]:
        warnings: list[str] = []

        room_ids = [room["unique_id"] for room in self.rooms]
        door_ids = [door["unique_id"] for door in self.doors]
        seen_room_ids = ids_seen_in_pdf(self.pdf_text, room_ids)
        seen_door_ids = ids_seen_in_pdf(self.pdf_text, door_ids)

        duplicate_ids = self._duplicate_ids(room_ids, door_ids)
        if duplicate_ids:
            warnings.append(f"Duplicate IDs in details file: {', '.join(duplicate_ids)}")

        warnings.extend(self._count_warnings())

        if self.require_text_identifiers:
            missing_room_ids = sorted(set(room_ids) - set(seen_room_ids))
            missing_door_ids = sorted(set(door_ids) - set(seen_door_ids))
            if missing_room_ids:
                warnings.append(f"Room IDs missing from PDF text: {', '.join(missing_room_ids[:12])}")
            if missing_door_ids:
                warnings.append(f"Door IDs missing from PDF text: {', '.join(missing_door_ids[:12])}")
        else:
            seen_room_ids = room_ids
            seen_door_ids = door_ids

        bad_door_ids = [
            door_id for door_id in door_ids if not re.fullmatch(r"D\d{3}[ULRD]", door_id)
        ]
        if bad_door_ids:
            warnings.append(f"Door IDs without direction suffix: {', '.join(bad_door_ids[:12])}")

        return {
            "ok": not warnings,
            "warnings": warnings,
            "rooms_in_details": len(self.rooms),
            "doors_in_details": len(self.doors),
            "room_ids_seen_in_pdf": len(seen_room_ids),
            "door_ids_seen_in_pdf": len(seen_door_ids),
        }

    def _duplicate_ids(self, room_ids: list[str], door_ids: list[str]) -> list[str]:
        return sorted(
            identifier
            for identifier in set(room_ids + door_ids)
            if room_ids.count(identifier) + door_ids.count(identifier) > 1
        )

    def _count_warnings(self) -> list[str]:
        warnings: list[str] = []
        expected_rooms = self.metadata.get("spaces_count")
        expected_doors = self.metadata.get("doors_count")
        if expected_rooms is not None and int(expected_rooms) != len(self.rooms):
            warnings.append(f"PDF says {expected_rooms} rooms, details file has {len(self.rooms)}")
        if expected_doors is not None and int(expected_doors) != len(self.doors):
            warnings.append(f"PDF says {expected_doors} doors, details file has {len(self.doors)}")
        return warnings


def validate_readback(
    pdf_text: str,
    metadata: dict[str, Any],
    rooms: list[dict[str, Any]],
    doors: list[dict[str, Any]],
    *,
    require_text_identifiers: bool = True,
) -> dict[str, Any]:
    return FloorPlanReadbackValidator(
        pdf_text=pdf_text,
        metadata=metadata,
        rooms=rooms,
        doors=doors,
        require_text_identifiers=require_text_identifiers,
    ).validate()


@dataclass(frozen=True)
class GeneratedFloorPlanReader:
    pdf_path: Path
    details_path: Path | None = None

    def read_payload(self) -> dict[str, Any]:
        document = PdfDocument(self.pdf_path)
        pdf_text = document.text()
        pdf_info = document.info()
        metadata = extract_pdf_metadata(pdf_text)
        scale = extract_scale_block(pdf_text)
        lookup, details_source, require_text_identifiers = self._read_lookup(document, pdf_text, metadata)
        validation = FloorPlanReadbackValidator(
            pdf_text=pdf_text,
            metadata=metadata,
            rooms=lookup["rooms"],
            doors=lookup["doors"],
            require_text_identifiers=require_text_identifiers,
        ).validate()

        return {
            "schema": "aau_map_upload_payload",
            "version": 1,
            "source_files": {
                "pdf": str(self.pdf_path),
                "details": str(self.details_path) if self.details_path else details_source,
            },
            "pdf": {
                "info": pdf_info,
                "metadata": metadata,
                "scale": scale,
            },
            "rooms": lookup["rooms"],
            "doors": lookup["doors"],
            "validation": validation,
        }

    def _read_lookup(
        self,
        document: PdfDocument,
        pdf_text: str,
        metadata: dict[str, Any],
    ) -> tuple[dict[str, Any], str, bool]:
        if self.details_path:
            return DetailsLookupReader(self.details_path).read(), "details-file", True

        embedded_lookup = document.embedded_details()
        if embedded_lookup:
            return embedded_lookup, "embedded-pdf-json", False

        return read_lookup_from_pdf_text(pdf_text, metadata), "embedded-pdf-text", True


def read_floor_payload(pdf_path: Path, details_path: Path | None = None) -> dict[str, Any]:
    return GeneratedFloorPlanReader(pdf_path, details_path).read_payload()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf",
        type=Path,
        required=True,
        help="Generated map PDF with embedded AAU_DETAILS rows.",
    )
    parser.add_argument("--details", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path for the future upload payload.",
    )
    args = parser.parse_args()

    payload = read_floor_payload(args.pdf, args.details)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    validation = payload["validation"]
    print(f"PDF: {args.pdf}")
    print(f"Details source: {args.details or 'embedded PDF text'}")
    print(
        "Scale: "
        f"{payload['pdf']['scale']['AAU_SCALE_PDF_POINTS_PER_METER']} PDF pt/m "
        "(from AAU_SCALE_BLOCK)"
    )
    print(
        "Read: "
        f"{validation['rooms_in_details']} rooms, {validation['doors_in_details']} doors"
    )
    print(
        "Seen in PDF text: "
        f"{validation['room_ids_seen_in_pdf']} room IDs, "
        f"{validation['door_ids_seen_in_pdf']} door IDs"
    )
    if validation["warnings"]:
        print("Warnings:")
        for warning in validation["warnings"]:
            print(f"- {warning}")
    else:
        print("Validation: OK")
    if args.output:
        print(args.output)


if __name__ == "__main__":
    main()
