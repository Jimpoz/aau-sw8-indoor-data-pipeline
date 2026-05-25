"""Render readable floor plans from Map API floor geometry."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from html import escape as html_escape
from typing import Any
from urllib.parse import quote

from campus_export import safe_file_name


ROOM_TYPE_COLORS = {
    "ROOM_GENERIC": "#B3D9FF",
    "ROOM_OFFICE": "#A8CCF0",
    "ROOM_CLASSROOM": "#B3D9FF",
    "ROOM_LECTURE_HALL": "#9EC5E8",
    "ROOM_LAB": "#8BB8E0",
    "ROOM_MEETING": "#B3D9FF",
    "ROOM_STORAGE": "#C8DFF5",
    "ROOM_UTILITY": "#C8DFF5",
    "CORRIDOR": "#E0E0E0",
    "CORRIDOR_SEGMENT": "#E0E0E0",
    "LOBBY": "#D8D8D8",
    "WAITING_AREA": "#D8D8D8",
    "RECEPTION": "#D8D8D8",
    "ENTRANCE": "#90EE90",
    "ENTRANCE_SECONDARY": "#A8F0A8",
    "EXIT_EMERGENCY": "#FFB3B3",
    "STAIRCASE": "#FFD699",
    "ELEVATOR": "#FFCC80",
    "ESCALATOR": "#FFD699",
    "RAMP": "#FFD699",
    "BRIDGE": "#FFD699",
    "TUNNEL": "#D0D0D0",
    "COVERED_WALKWAY": "#D8E8D8",
    "OUTDOOR_PATH": "#C8F0C8",
    "OUTDOOR_PLAZA": "#C8F0C8",
    "OUTDOOR_COURTYARD": "#C8F0C8",
    "OUTDOOR_STAIRS": "#C8F0C8",
    "PARKING": "#D0E8D0",
    "RESTROOM": "#FFB3D9",
    "RESTROOM_ACCESSIBLE": "#FFB3D9",
    "CAFETERIA": "#FFEB99",
    "CAFE": "#FFEB99",
    "LIBRARY": "#FFEB99",
    "GYM": "#FFEB99",
    "AUDITORIUM": "#FFEB99",
    "SHOP": "#FFEB99",
    "INACCESSIBLE": "#808080",
}
DEFAULT_ROOM_COLOR = ROOM_TYPE_COLORS["ROOM_GENERIC"]
DOOR_COLOR = "#CC2F2F"
STROKE_COLOR = "#25313D"
TEXT_COLOR = "#25313D"
ROOM_LABEL_FONT_SIZE = 6.0
ROOM_LABEL_WIDTH_FACTOR = 0.72
ROOM_LABEL_HEIGHT_FACTOR = 0.64
ROOM_LABEL_CHAR_WIDTH_FACTOR = 0.62
ROOM_LABEL_LINE_HEIGHT_FACTOR = 1.35
DOOR_MARK_SIZE_M = 0.35
DOOR_MARK_DRAW_SIZE_M = 0.16
DIMENSION_LABEL_DA = "H X B"
DOOR_COUNT_SINGULAR_DA = "DØR"
DOOR_COUNT_PLURAL_DA = "DØRE"


def point_pair(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict):
        x = value.get("x")
        y = value.get("y")
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        x, y = value[0], value[1]
    else:
        return None
    if x is None or y is None:
        return None
    return float(x), float(y)


def space_polygon(space: dict[str, Any]) -> list[tuple[float, float]]:
    points = []
    for value in space.get("polygon") or []:
        point = point_pair(value)
        if point:
            points.append(point)
    return points


def polygon_center(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return 0.0, 0.0
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def floor_bounds(spaces: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    points = [point for space in spaces for point in space_polygon(space)]
    if not points:
        raise ValueError("Selected floor has no space polygons to draw")
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def drawing_transform(
    spaces: list[dict[str, Any]],
    *,
    connections: list[dict[str, Any]] | None = None,
    min_width: float = 1200.0,
    min_height: float = 850.0,
    margin: float = 40.0,
) -> tuple[float, float, float, float, float]:
    min_x, min_y, max_x, max_y = floor_bounds(spaces)
    width_m = max(max_x - min_x, 1.0)
    height_m = max(max_y - min_y, 1.0)
    scale = max((min_width - margin * 2) / width_m, (min_height - margin * 2) / height_m)
    if connections is not None:
        scale = max(scale, label_required_scale(spaces, connections))
    width = width_m * scale + margin * 2
    height = height_m * scale + margin * 2
    return min_x, min_y, scale, width, height


def unique_doors(connections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    doors: dict[str, dict[str, Any]] = {}
    for conn in connections:
        door_id = conn.get("door_id")
        if not door_id:
            continue
        if door_id not in doors:
            doors[str(door_id)] = conn
    return list(doors.values())


@dataclass(frozen=True)
class FloorDrawingContext:
    building: dict[str, Any]
    floor: dict[str, Any]
    spaces: list[dict[str, Any]]
    connections: list[dict[str, Any]]
    min_x: float
    min_y: float
    scale: float
    width: float
    height: float
    margin: float = 40.0

    @classmethod
    def from_floor(
        cls,
        *,
        building: dict[str, Any],
        floor: dict[str, Any],
        spaces: list[dict[str, Any]],
        connections: list[dict[str, Any]],
        margin: float = 40.0,
    ) -> "FloorDrawingContext":
        min_x, min_y, scale, width, height = drawing_transform(
            spaces,
            connections=connections,
            margin=margin,
        )
        return cls(
            building=building,
            floor=floor,
            spaces=spaces,
            connections=connections,
            min_x=min_x,
            min_y=min_y,
            scale=scale,
            width=width,
            height=height,
            margin=margin,
        )

    @property
    def spaces_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(space.get("id")): space for space in self.spaces if space.get("id")}

    @property
    def doors(self) -> list[dict[str, Any]]:
        return unique_doors(self.connections)

    def xy_top_left(self, point: tuple[float, float]) -> tuple[float, float]:
        return (
            (point[0] - self.min_x) * self.scale + self.margin,
            (point[1] - self.min_y) * self.scale + self.margin,
        )

    def xy_bottom_left(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = self.xy_top_left(point)
        return x, self.height - y

    def door_placements(
        self,
        *,
        xy: Any,
        font_size: float,
        line_gap: float,
        padding: float,
    ) -> list[dict[str, Any]]:
        return door_label_placements(
            doors=self.doors,
            spaces=self.spaces,
            spaces_by_id=self.spaces_by_id,
            xy=xy,
            width=self.width,
            height=self.height,
            font_size=font_size,
            line_gap=line_gap,
            padding=padding,
        )

    def metadata_lines(self) -> list[str]:
        return scale_metadata_lines(
            building=self.building,
            floor=self.floor,
            spaces=self.spaces,
            connections=self.connections,
            min_x=self.min_x,
            min_y=self.min_y,
            scale=self.scale,
            width=self.width,
            height=self.height,
            margin=self.margin,
        )

    def embedded_details(self) -> dict[str, Any]:
        return embedded_pdf_details(
            building=self.building,
            floor=self.floor,
            spaces=self.spaces,
            connections=self.connections,
        )


def is_door_space(space: dict[str, Any]) -> bool:
    return str(space.get("space_type", "")).startswith("DOOR")


def room_fill_color(space: dict[str, Any]) -> str:
    if is_door_space(space):
        return DOOR_COLOR
    return ROOM_TYPE_COLORS.get(str(space.get("space_type") or "ROOM_GENERIC"), DEFAULT_ROOM_COLOR)


def hex_to_rgb_float(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return (
        int(value[0:2], 16) / 255.0,
        int(value[2:4], 16) / 255.0,
        int(value[4:6], 16) / 255.0,
    )


def hex_to_bgr(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[4:6], 16), int(value[2:4], 16), int(value[0:2], 16)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def polygon_bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def room_dimensions(points: list[tuple[float, float]]) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = polygon_bounds(points)
    return max_y - min_y, max_x - min_x


def connections_for_space(
    connections: list[dict[str, Any]],
    space_id: str | None,
) -> list[dict[str, Any]]:
    if not space_id:
        return []
    return [
        conn
        for conn in connections
        if conn.get("from_space_id") == space_id or conn.get("to_space_id") == space_id
    ]


def door_count_for_space(connections: list[dict[str, Any]], space_id: str | None) -> int:
    return len(
        {
            str(conn.get("door_id"))
            for conn in connections_for_space(connections, space_id)
            if conn.get("door_id")
        }
    )


def room_label_lines(
    space: dict[str, Any],
    points: list[tuple[float, float]],
    connections: list[dict[str, Any]],
) -> list[str]:
    height_m, width_m = room_dimensions(points)
    door_count = door_count_for_space(connections, space.get("id"))
    door_word = DOOR_COUNT_SINGULAR_DA if door_count == 1 else DOOR_COUNT_PLURAL_DA
    return [
        str(space.get("display_name") or space.get("name") or space.get("id") or ""),
        f"{DIMENSION_LABEL_DA} {height_m:.1f} X {width_m:.1f} M",
        f"{door_count} {door_word}",
    ]


def label_required_scale(
    spaces: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    *,
    font_size: float = ROOM_LABEL_FONT_SIZE,
) -> float:
    required = 0.0
    for space in spaces:
        if is_door_space(space):
            continue
        points = space_polygon(space)
        if len(points) < 3:
            continue
        min_x, min_y, max_x, max_y = polygon_bounds(points)
        width_m = max(max_x - min_x, 0.05)
        height_m = max(max_y - min_y, 0.05)
        lines = [line for line in room_label_lines(space, points, connections) if line]
        if not lines:
            continue
        longest = max(len(line) for line in lines)
        required = max(
            required,
            (longest * font_size * ROOM_LABEL_CHAR_WIDTH_FACTOR) / (width_m * ROOM_LABEL_WIDTH_FACTOR),
            (len(lines) * font_size * ROOM_LABEL_LINE_HEIGHT_FACTOR) / (height_m * ROOM_LABEL_HEIGHT_FACTOR),
        )
    return required


def fitted_label_layout(
    points: list[tuple[float, float]],
    scale: float,
    lines: list[str],
    *,
    font_size: float = ROOM_LABEL_FONT_SIZE,
) -> tuple[list[str], float, float, float]:
    min_x, _, max_x, _ = polygon_bounds(points)
    box_width = max((max_x - min_x) * scale * ROOM_LABEL_WIDTH_FACTOR, 1.0)
    visible = [line for line in lines if line]
    return visible, font_size, font_size * 1.18, box_width


def point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
    nearest_x = x1 + t * dx
    nearest_y = y1 + t * dy
    return ((px - nearest_x) ** 2 + (py - nearest_y) ** 2) ** 0.5


def room_wall_distance_m(
    point: tuple[float, float],
    space: dict[str, Any],
) -> float | None:
    polygon = space_polygon(space)
    if len(polygon) < 2:
        return None
    closed = polygon if polygon[0] == polygon[-1] else [*polygon, polygon[0]]
    return min(point_segment_distance(point, start, end) for start, end in zip(closed, closed[1:]))


def nearest_wall_distance_m(
    door: dict[str, Any],
    spaces_by_id: dict[str, dict[str, Any]],
) -> float | None:
    cx = door.get("door_cx")
    cy = door.get("door_cy")
    if cx is None or cy is None:
        return None
    point = (float(cx), float(cy))
    distances: list[float] = []
    for space in spaces_by_id.values():
        if is_door_space(space):
            continue
        distance = room_wall_distance_m(point, space)
        if distance is not None:
            distances.append(distance)
    return min(distances) if distances else None


def connected_room_ids_for_door(
    door: dict[str, Any],
    spaces_by_id: dict[str, dict[str, Any]],
    *,
    limit: int = 2,
    max_distance_m: float = 1.25,
) -> list[str]:
    room_ids: list[str] = []
    for space_id in (door.get("from_space_id"), door.get("to_space_id")):
        value = str(space_id) if space_id is not None else ""
        space = spaces_by_id.get(value)
        if not value or not space or is_door_space(space) or value in room_ids:
            continue
        room_ids.append(value)

    cx = door.get("door_cx")
    cy = door.get("door_cy")
    if len(room_ids) >= limit or cx is None or cy is None:
        return room_ids[:limit]

    point = (float(cx), float(cy))
    candidates: list[tuple[float, float, str]] = []
    for space_id, space in spaces_by_id.items():
        if space_id in room_ids or is_door_space(space):
            continue
        distance = room_wall_distance_m(point, space)
        if distance is None or distance > max_distance_m:
            continue
        center_distance = sum((a - b) ** 2 for a, b in zip(point, polygon_center(space_polygon(space)))) ** 0.5
        candidates.append((distance, center_distance, space_id))

    for _, _, space_id in sorted(candidates):
        if len(room_ids) >= limit:
            break
        room_ids.append(space_id)

    return room_ids[:limit]


def door_orientation(door: dict[str, Any], spaces_by_id: dict[str, dict[str, Any]]) -> str:
    from_space = spaces_by_id.get(str(door.get("from_space_id")))
    to_space = spaces_by_id.get(str(door.get("to_space_id")))
    if from_space and to_space:
        from_points = space_polygon(from_space)
        to_points = space_polygon(to_space)
        if from_points and to_points:
            from_center = polygon_center(from_points)
            to_center = polygon_center(to_points)
            dx = abs(from_center[0] - to_center[0])
            dy = abs(from_center[1] - to_center[1])
            return "vertical" if dx >= dy else "horizontal"
    return "horizontal"


def door_label(index: int, door: dict[str, Any], orientation: str) -> str:
    value = str(door.get("door_id") or "").strip()
    if value and len(value) <= 12 and value[-1:].upper() in {"U", "L", "R", "D"}:
        return value
    return f"D{index:03d}U"


def door_label_lines(
    index: int,
    door: dict[str, Any],
    spaces_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    distance = nearest_wall_distance_m(door, spaces_by_id)
    orientation = door_orientation(door, spaces_by_id)
    lines = [door_label(index, door, orientation)]
    if distance is not None:
        lines.append(f"{distance:.1f} M")
    return lines


def estimated_text_width(text: str, font_size: float) -> float:
    return max(1.0, len(text) * font_size * 0.55)


def door_label_box_size(
    lines: list[str],
    *,
    font_size: float,
    line_gap: float,
    padding: float,
) -> tuple[float, float]:
    width = max(estimated_text_width(line, font_size) for line in lines) + padding * 2
    height = font_size + line_gap * max(len(lines) - 1, 0) + padding * 2
    return width, height


def label_candidate_rects(
    x: float,
    y: float,
    label_width: float,
    label_height: float,
    gap: float,
) -> list[tuple[float, float, float, float]]:
    return [
        (x + gap, y - label_height / 2, x + gap + label_width, y + label_height / 2),
        (x - gap - label_width, y - label_height / 2, x - gap, y + label_height / 2),
        (x - label_width / 2, y + gap, x + label_width / 2, y + gap + label_height),
        (x - label_width / 2, y - gap - label_height, x + label_width / 2, y - gap),
        (x + gap, y + gap, x + gap + label_width, y + gap + label_height),
        (x + gap, y - gap - label_height, x + gap + label_width, y - gap),
        (x - gap - label_width, y + gap, x - gap, y + gap + label_height),
        (x - gap - label_width, y - gap - label_height, x - gap, y - gap),
    ]


def point_in_rect(point: tuple[float, float], rect: tuple[float, float, float, float]) -> bool:
    x, y = point
    x0, y0, x1, y1 = rect
    return x0 <= x <= x1 and y0 <= y <= y1


def segment_orientation(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])


def point_on_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    return (
        min(a[0], c[0]) <= b[0] <= max(a[0], c[0])
        and min(a[1], c[1]) <= b[1] <= max(a[1], c[1])
    )


def segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    o1 = segment_orientation(a1, a2, b1)
    o2 = segment_orientation(a1, a2, b2)
    o3 = segment_orientation(b1, b2, a1)
    o4 = segment_orientation(b1, b2, a2)
    eps = 1e-9
    if abs(o1) < eps and point_on_segment(a1, b1, a2):
        return True
    if abs(o2) < eps and point_on_segment(a1, b2, a2):
        return True
    if abs(o3) < eps and point_on_segment(b1, a1, b2):
        return True
    if abs(o4) < eps and point_on_segment(b1, a2, b2):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def segment_intersects_rect(
    segment: tuple[tuple[float, float], tuple[float, float]],
    rect: tuple[float, float, float, float],
) -> bool:
    start, end = segment
    if point_in_rect(start, rect) or point_in_rect(end, rect):
        return True
    x0, y0, x1, y1 = rect
    edges = [
        ((x0, y0), (x1, y0)),
        ((x1, y0), (x1, y1)),
        ((x1, y1), (x0, y1)),
        ((x0, y1), (x0, y0)),
    ]
    return any(segments_intersect(start, end, edge_start, edge_end) for edge_start, edge_end in edges)


def rect_overlap_area(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def inflate_rect(
    rect: tuple[float, float, float, float],
    amount: float,
) -> tuple[float, float, float, float]:
    return (rect[0] - amount, rect[1] - amount, rect[2] + amount, rect[3] + amount)


def rect_outside_area(
    rect: tuple[float, float, float, float],
    width: float,
    height: float,
    *,
    margin: float = 3.0,
) -> float:
    x0, y0, x1, y1 = rect
    outside_x = max(margin - x0, 0.0) + max(x1 - (width - margin), 0.0)
    outside_y = max(margin - y0, 0.0) + max(y1 - (height - margin), 0.0)
    return outside_x + outside_y


def wall_segments_for_spaces(
    spaces: list[dict[str, Any]],
    xy: Any,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for space in spaces:
        if is_door_space(space):
            continue
        points = space_polygon(space)
        if len(points) < 2:
            continue
        transformed = [xy(point) for point in points]
        closed = transformed if transformed[0] == transformed[-1] else [*transformed, transformed[0]]
        segments.extend(zip(closed, closed[1:]))
    return segments


def door_label_placements(
    *,
    doors: list[dict[str, Any]],
    spaces: list[dict[str, Any]],
    spaces_by_id: dict[str, dict[str, Any]],
    xy: Any,
    width: float,
    height: float,
    font_size: float,
    line_gap: float,
    padding: float,
) -> list[dict[str, Any]]:
    walls = wall_segments_for_spaces(spaces, xy)
    occupied: list[tuple[float, float, float, float]] = []
    placements: list[dict[str, Any]] = []
    for index, door in enumerate(doors, start=1):
        cx = door.get("door_cx")
        cy = door.get("door_cy")
        if cx is None or cy is None:
            continue
        x, y = xy((float(cx), float(cy)))
        lines = door_label_lines(index, door, spaces_by_id)
        label_width, label_height = door_label_box_size(
            lines,
            font_size=font_size,
            line_gap=line_gap,
            padding=padding,
        )
        base_gap = max(label_height * 0.6, 8.0)
        best_rect: tuple[float, float, float, float] | None = None
        best_score: float | None = None
        candidates: list[tuple[float, float, float, float]] = []
        for extra_gap in (0.0, label_height + 6.0, label_height * 2.0 + 12.0):
            candidates.extend(label_candidate_rects(x, y, label_width, label_height, base_gap + extra_gap))
        for preference, rect in enumerate(candidates):
            padded = inflate_rect(rect, 1.0)
            wall_hits = sum(1 for segment in walls if segment_intersects_rect(segment, padded))
            overlap = sum(rect_overlap_area(inflate_rect(rect, 2.0), other) for other in occupied)
            outside = rect_outside_area(rect, width, height)
            center_distance = ((x - (rect[0] + rect[2]) / 2) ** 2 + (y - (rect[1] + rect[3]) / 2) ** 2) ** 0.5
            score = wall_hits * 100000.0 + outside * 10000.0 + overlap * 15.0 + center_distance + preference
            if best_score is None or score < best_score:
                best_rect = rect
                best_score = score
        if best_rect is None:
            continue
        occupied.append(inflate_rect(best_rect, 2.0))
        placements.append(
            {
                "index": index,
                "door": door,
                "x": x,
                "y": y,
                "lines": lines,
                "rect": best_rect,
                "font_size": font_size,
                "line_gap": line_gap,
                "padding": padding,
            }
        )
    return placements


def embedded_pdf_details(
    *,
    building: dict[str, Any],
    floor: dict[str, Any],
    spaces: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> dict[str, Any]:
    spaces_by_id = {str(space.get("id")): space for space in spaces if space.get("id")}
    doors = unique_doors(connections)
    door_ids_by_space: dict[str, list[str]] = {}
    detail_doors: list[dict[str, Any]] = []

    for index, door in enumerate(doors, start=1):
        orientation = door_orientation(door, spaces_by_id)
        unique_id = door_label(index, door, orientation)
        connected_space_ids = connected_room_ids_for_door(door, spaces_by_id)
        for space_id in connected_space_ids:
            door_ids_by_space.setdefault(space_id, []).append(unique_id)
        direction = unique_id[-1:] if unique_id[-1:] in {"U", "L", "R", "D"} else ""
        distance = nearest_wall_distance_m(door, spaces_by_id)
        detail_doors.append(
            {
                "unique_id": unique_id,
                "base_id": unique_id[:-1] if direction else unique_id,
                "source_id": str(door.get("door_id") or unique_id),
                "name": unique_id,
                "type": "DOOR",
                "building": str(building.get("name", "")),
                "floor": str(floor.get("display_name", "")),
                "floor_index": int(floor.get("floor_index") or 0),
                "centroid_m": {
                    "x": round(float(door.get("door_cx") or 0.0), 4),
                    "y": round(float(door.get("door_cy") or 0.0), 4),
                },
                "direction": direction,
                "orientation": orientation,
                "size_m": DOOR_MARK_SIZE_M,
                "wall_length_m": None if distance is None else round(distance, 4),
                "nearest_wall_distance_m": None if distance is None else round(distance, 4),
                "reference_room": {
                    "unique_id": connected_space_ids[0] if connected_space_ids else "",
                    "source_id": connected_space_ids[0] if connected_space_ids else "",
                    "name": spaces_by_id.get(connected_space_ids[0], {}).get("display_name", "")
                    if connected_space_ids
                    else "",
                },
                "connected_rooms": [
                    {
                        "unique_id": space_id,
                        "source_id": space_id,
                        "name": str(spaces_by_id.get(space_id, {}).get("display_name", space_id)),
                    }
                    for space_id in connected_space_ids
                ],
            }
        )

    detail_rooms: list[dict[str, Any]] = []
    for space in spaces:
        if is_door_space(space):
            continue
        points = space_polygon(space)
        if len(points) < 3:
            continue
        min_x, min_y, max_x, max_y = polygon_bounds(points)
        width_m = max_x - min_x
        height_m = max_y - min_y
        centroid_x, centroid_y = polygon_center(points)
        room_id = str(space.get("id") or safe_file_name(str(space.get("display_name") or "")))
        room_door_ids = sorted(door_ids_by_space.get(room_id, []))
        detail_rooms.append(
            {
                "unique_id": room_id,
                "source_id": str(space.get("id") or room_id),
                "name": str(space.get("display_name") or room_id),
                "type": str(space.get("space_type") or "ROOM_GENERIC"),
                "building": str(building.get("name", "")),
                "floor": str(floor.get("display_name", "")),
                "floor_index": int(floor.get("floor_index") or 0),
                "centroid_m": {"x": round(centroid_x, 4), "y": round(centroid_y, 4)},
                "dimensions_m": {"width": round(width_m, 4), "height": round(height_m, 4)},
                "area_m2": round(width_m * height_m, 4),
                "polygon_m": [[round(x, 4), round(y, 4)] for x, y in points],
                "door_count": len(room_door_ids),
                "doors": room_door_ids,
            }
        )

    return {
        "schema": "aau_map_details",
        "version": 1,
        "building": str(building.get("name", "")),
        "floor": str(floor.get("display_name", "")),
        "floor_index": int(floor.get("floor_index") or 0),
        "rooms": detail_rooms,
        "doors": detail_doors,
    }


def append_pdf_details_block(pdf_bytes: bytes, details: dict[str, Any]) -> bytes:
    payload = json.dumps(details, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(payload)
    return (
        pdf_bytes
        + b"\n%AAU_DETAILS_JSON_BEGIN\n%"
        + encoded
        + b"\n%AAU_DETAILS_JSON_END\n"
    )


def scale_metadata_lines(
    *,
    building: dict[str, Any],
    floor: dict[str, Any],
    spaces: list[dict[str, Any]],
    connections: list[dict[str, Any]],
    min_x: float,
    min_y: float,
    scale: float,
    width: float,
    height: float,
    margin: float = 40.0,
) -> list[str]:
    max_x = max(point[0] for space in spaces for point in space_polygon(space))
    max_y = max(point[1] for space in spaces for point in space_polygon(space))
    metadata = (
        f"AAU_MAP_META_BUILDING={quote(str(building.get('name', '')))} "
        f"AAU_MAP_META_FLOOR_INDEX={int(floor.get('floor_index') or 0)} "
        f"AAU_MAP_META_FLOOR={quote(str(floor.get('display_name', '')))} "
        f"AAU_MAP_META_SPACES_COUNT={len(spaces)} "
        f"AAU_MAP_META_DOORS_COUNT={len(unique_doors(connections))}"
    )
    return [
        metadata,
        "AAU_SCALE_BLOCK_BEGIN",
        f"AAU_SCALE_PDF_POINTS_PER_METER={scale:.8f}",
        f"AAU_SCALE_BOUNDS_MIN_X_M={min_x:.8f}",
        f"AAU_SCALE_BOUNDS_MIN_Y_M={min_y:.8f}",
        f"AAU_SCALE_BOUNDS_MAX_X_M={max_x:.8f}",
        f"AAU_SCALE_BOUNDS_MAX_Y_M={max_y:.8f}",
        f"AAU_SCALE_FRAME_X_PT={margin:.8f}",
        f"AAU_SCALE_FRAME_Y_PT={margin:.8f}",
        f"AAU_SCALE_FRAME_WIDTH_PT={width - margin * 2:.8f}",
        f"AAU_SCALE_FRAME_HEIGHT_PT={height - margin * 2:.8f}",
        "AAU_SCALE_BLOCK_END",
    ]


def build_floor_svg(
    *,
    building: dict[str, Any],
    floor: dict[str, Any],
    spaces: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> str:
    context = FloorDrawingContext.from_floor(
        building=building,
        floor=floor,
        spaces=spaces,
        connections=connections,
    )
    xy = context.xy_top_left
    door_placements = context.door_placements(
        xy=xy,
        font_size=4.5,
        line_gap=5.5,
        padding=2.4,
    )

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{context.width:.1f}" '
            f'height="{context.height:.1f}" viewBox="0 0 {context.width:.1f} {context.height:.1f}">'
        ),
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        (
            f'<text x="24" y="28" font-family="Arial, sans-serif" font-size="16" '
            f'font-weight="700" fill="#1f2933">{html_escape(str(building.get("name", "")))} '
            f'/ {html_escape(str(floor.get("display_name", "")))}</text>'
        ),
        "<metadata>",
        html_escape(
            json.dumps(
                {
                    "schema": "aau_map_floor_plan",
                    "building": building.get("name"),
                    "floor": floor.get("display_name"),
                    "spaces": len(spaces),
                    "doors": len(context.doors),
                },
                ensure_ascii=False,
            )
        ),
        "</metadata>",
    ]
    parts.extend(f"<!-- {html_escape(line)} -->" for line in context.metadata_lines())

    for space_index, space in enumerate(spaces, start=1):
        points = space_polygon(space)
        if len(points) < 3:
            continue
        svg_points = " ".join(f"{x:.2f},{y:.2f}" for x, y in (xy(point) for point in points))
        label_x, label_y = xy(polygon_center(points))
        raw_lines = room_label_lines(space, points, connections)
        visible_lines, font_size, line_gap, _ = fitted_label_layout(points, context.scale, raw_lines)
        lines = [html_escape(line) for line in visible_lines]
        fill = room_fill_color(space)
        title = html_escape(" | ".join(raw_lines))
        parts.append(
            f'<polygon points="{svg_points}" fill="{fill}" stroke="{STROKE_COLOR}" '
            f'stroke-width="1.2"><title>{title}</title></polygon>'
        )
        if is_door_space(space) or not lines:
            continue
        clip_id = f"room-label-clip-{space_index}"
        parts.append(f'<clipPath id="{clip_id}"><polygon points="{svg_points}"/></clipPath>')
        start_y = label_y - ((len(lines) - 1) * line_gap / 2)
        tspans = []
        for line_index, line in enumerate(lines):
            dy = "0" if line_index == 0 else f"{line_gap:.2f}"
            tspans.append(f'<tspan x="{label_x:.2f}" dy="{dy}">{line}</tspan>')
        parts.append(
            f'<text x="{label_x:.2f}" y="{start_y:.2f}" text-anchor="middle" '
            f'clip-path="url(#{clip_id})" font-family="Arial, sans-serif" '
            f'font-size="{font_size:.2f}" fill="{TEXT_COLOR}">'
            f"{''.join(tspans)}</text>"
        )

    for placement in door_placements:
        x, y = placement["x"], placement["y"]
        marker_size = max(2.5, min(5.0, DOOR_MARK_DRAW_SIZE_M * context.scale))
        marker_half = marker_size / 2
        title = html_escape(" | ".join(placement["lines"]))
        parts.append(
            f'<rect x="{x - marker_half:.2f}" y="{y - marker_half:.2f}" '
            f'width="{marker_size:.2f}" height="{marker_size:.2f}" '
            f'fill="{DOOR_COLOR}"><title>{title}</title></rect>'
        )

    for placement in door_placements:
        x0, y0, _, _ = placement["rect"]
        padding = placement["padding"]
        lines = [html_escape(line) for line in placement["lines"]]
        text_x = x0 + padding
        text_y = y0 + padding + placement["font_size"]
        tspans = []
        for line_index, line in enumerate(lines):
            dy = "0" if line_index == 0 else f'{placement["line_gap"]:.2f}'
            tspans.append(f'<tspan x="{text_x:.2f}" dy="{dy}">{line}</tspan>')
        parts.append(
            f'<text x="{text_x:.2f}" y="{text_y:.2f}" '
            f'font-family="Arial, sans-serif" font-size="4.5" fill="{TEXT_COLOR}">'
            f"{''.join(tspans)}</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf_bytes(width: float, height: float, commands: str) -> bytes:
    stream = commands.encode("cp1252", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.2f} {height:.2f}] "
            "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode("ascii"))
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    body.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(body)


def build_floor_pdf(
    *,
    building: dict[str, Any],
    floor: dict[str, Any],
    spaces: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> bytes:
    context = FloorDrawingContext.from_floor(
        building=building,
        floor=floor,
        spaces=spaces,
        connections=connections,
    )
    xy = context.xy_bottom_left
    door_placements = context.door_placements(
        xy=xy,
        font_size=3.2,
        line_gap=4.2,
        padding=1.8,
    )

    def set_fill(color: str) -> str:
        r, g, b = hex_to_rgb_float(color)
        return f"{r:.3f} {g:.3f} {b:.3f} rg"

    def set_stroke(color: str) -> str:
        r, g, b = hex_to_rgb_float(color)
        return f"{r:.3f} {g:.3f} {b:.3f} RG"

    def text_line(
        text: str,
        x: float,
        y: float,
        *,
        font_size: float,
        color: str = TEXT_COLOR,
        center: bool = False,
        invisible: bool = False,
    ) -> str:
        safe = pdf_escape(text)
        draw_x = x
        if center:
            draw_x = x - len(text) * font_size * ROOM_LABEL_CHAR_WIDTH_FACTOR / 2
        render_mode = "3 Tr" if invisible else "0 Tr"
        return (
            f"{set_fill(color)} BT {render_mode} /F1 {font_size:.2f} Tf "
            f"{draw_x:.2f} {y:.2f} Td ({safe}) Tj ET"
        )

    commands: list[str] = [
        "1 1 1 rg 0 0 {0:.2f} {1:.2f} re f".format(context.width, context.height),
        text_line(
            f"{building.get('name', '')} / {floor.get('display_name', '')}",
            24,
            context.height - 28,
            font_size=14,
            color="#1F2933",
        ),
    ]
    metadata_lines = context.metadata_lines()
    for index, line in enumerate(metadata_lines):
        y = 4 + (len(metadata_lines) - index - 1) * 2.2
        commands.append(text_line(line, 4, y, font_size=1, invisible=True))

    for space in spaces:
        points = space_polygon(space)
        if len(points) < 3:
            continue
        transformed = [xy(point) for point in points]
        first = transformed[0]
        path = [f"{first[0]:.2f} {first[1]:.2f} m"]
        path.extend(f"{x:.2f} {y:.2f} l" for x, y in transformed[1:])
        path.append("h")
        commands.append(
            f"{set_fill(room_fill_color(space))} {set_stroke(STROKE_COLOR)} "
            "0.8 w " + " ".join(path) + " B"
        )
        if is_door_space(space):
            continue
        label_x, label_y = xy(polygon_center(points))
        lines, font_size, line_gap, _ = fitted_label_layout(
            points,
            context.scale,
            room_label_lines(space, points, connections),
        )
        if not lines:
            continue
        start_y = label_y + ((len(lines) - 1) * line_gap / 2)
        commands.append("q " + " ".join(path) + " W n")
        for line_index, line in enumerate(lines):
            commands.append(
                text_line(
                    line,
                    label_x,
                    start_y - line_index * line_gap,
                    font_size=font_size,
                    center=True,
                )
            )
        commands.append("Q")

    for placement in door_placements:
        x, y = placement["x"], placement["y"]
        marker_size = max(2.5, min(5.0, DOOR_MARK_DRAW_SIZE_M * context.scale))
        marker_half = marker_size / 2
        commands.append(
            f"{set_fill(DOOR_COLOR)} {x - marker_half:.2f} {y - marker_half:.2f} "
            f"{marker_size:.2f} {marker_size:.2f} re f"
        )

    for placement in door_placements:
        x0, _, _, y1 = placement["rect"]
        padding = placement["padding"]
        for line_index, line in enumerate(placement["lines"]):
            commands.append(
                text_line(
                    line,
                    x0 + padding,
                    y1 - padding - placement["font_size"] - line_index * placement["line_gap"],
                    font_size=placement["font_size"],
                    color=TEXT_COLOR,
                )
            )

    pdf_bytes = build_pdf_bytes(context.width, context.height, "\n".join(commands))
    return append_pdf_details_block(pdf_bytes, context.embedded_details())


def build_floor_png(
    *,
    building: dict[str, Any],
    floor: dict[str, Any],
    spaces: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> bytes:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    context = FloorDrawingContext.from_floor(
        building=building,
        floor=floor,
        spaces=spaces,
        connections=connections,
    )
    font_cache: dict[tuple[int, bool], Any] = {}

    def xy(point: tuple[float, float]) -> tuple[int, int]:
        x, y = context.xy_top_left(point)
        return int(round(x)), int(round(y))

    door_placements = context.door_placements(
        xy=context.xy_top_left,
        font_size=8.0,
        line_gap=9.0,
        padding=3.0,
    )

    def pil_font(size: int, *, bold: bool = False) -> Any:
        key = (size, bold)
        if key in font_cache:
            return font_cache[key]
        names = (
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
        for name in names:
            try:
                font_cache[key] = ImageFont.truetype(name, size)
                return font_cache[key]
            except OSError:
                continue
        font_cache[key] = ImageFont.load_default()
        return font_cache[key]

    def draw_pil_text(
        draw: Any,
        text: str,
        position: tuple[int, int],
        *,
        font_size: int,
        color: str = TEXT_COLOR,
        bold: bool = False,
        anchor: str | None = None,
    ) -> None:
        draw.text(
            position,
            text,
            fill=hex_to_rgb(color),
            font=pil_font(font_size, bold=bold),
            anchor=anchor,
        )

    def draw_text(
        target: Any,
        text: str,
        position: tuple[int, int],
        *,
        font_size: int,
        color: str = TEXT_COLOR,
        bold: bool = False,
        anchor: str | None = None,
    ) -> None:
        pil_image = Image.fromarray(cv2.cvtColor(target, cv2.COLOR_BGR2RGB))
        draw_pil_text(
            ImageDraw.Draw(pil_image),
            text,
            position,
            font_size=font_size,
            color=color,
            bold=bold,
            anchor=anchor,
        )
        target[:] = cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)

    image = np.full((int(round(context.height)), int(round(context.width)), 3), 255, dtype=np.uint8)
    draw_text(
        image,
        f"{building.get('name', '')} / {floor.get('display_name', '')}",
        (24, 28),
        font_size=16,
        color="#1F2933",
        bold=True,
    )

    for space in spaces:
        points = space_polygon(space)
        if len(points) < 3:
            continue
        pts = np.array([xy(point) for point in points], dtype=np.int32)
        cv2.fillPoly(image, [pts], hex_to_bgr(room_fill_color(space)))
        cv2.polylines(image, [pts], isClosed=True, color=hex_to_bgr(STROKE_COLOR), thickness=2)
        if is_door_space(space):
            continue
        label_x, label_y = xy(polygon_center(points))
        lines, font_size, line_gap_float, _ = fitted_label_layout(
            points,
            context.scale,
            room_label_lines(space, points, connections),
        )
        if not lines:
            continue
        line_gap = max(4, int(round(line_gap_float)))
        start_y = int(round(label_y - ((len(lines) - 1) * line_gap / 2)))
        overlay = image.copy()
        overlay_pil = Image.fromarray(cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB))
        overlay_draw = ImageDraw.Draw(overlay_pil)
        for line_index, line in enumerate(lines):
            draw_pil_text(
                overlay_draw,
                line,
                (int(round(label_x)), start_y + line_index * line_gap),
                font_size=max(7, int(round(font_size))),
                anchor="mm",
            )
        overlay = cv2.cvtColor(np.asarray(overlay_pil), cv2.COLOR_RGB2BGR)
        label_mask = np.zeros(image.shape[:2], dtype=np.uint8)
        cv2.fillPoly(label_mask, [pts], 255)
        image[label_mask > 0] = overlay[label_mask > 0]

    for placement in door_placements:
        x, y = int(round(placement["x"])), int(round(placement["y"]))
        marker_half = max(2, min(3, int(round(DOOR_MARK_DRAW_SIZE_M * context.scale / 2))))
        cv2.rectangle(
            image,
            (x - marker_half, y - marker_half),
            (x + marker_half, y + marker_half),
            hex_to_bgr(DOOR_COLOR),
            -1,
        )

    door_text_items: list[tuple[str, tuple[int, int], int]] = []
    for placement in door_placements:
        x0, y0, _, _ = (int(round(value)) for value in placement["rect"])
        padding = int(round(placement["padding"]))
        for line_index, line in enumerate(placement["lines"]):
            door_text_items.append(
                (
                    line,
                    (x0 + padding, y0 + padding + line_index * int(round(placement["line_gap"]))),
                    int(round(placement["font_size"])),
                )
            )

    if door_text_items:
        image_pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        image_draw = ImageDraw.Draw(image_pil)
        for line, position, font_size in door_text_items:
            draw_pil_text(image_draw, line, position, font_size=font_size)
        image = cv2.cvtColor(np.asarray(image_pil), cv2.COLOR_RGB2BGR)

    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Could not encode the floor plan")
    return encoded.tobytes()


@dataclass(frozen=True)
class FloorPlanRenderer:
    def render(
        self,
        *,
        output_format: str,
        building: dict[str, Any],
        floor: dict[str, Any],
        spaces: list[dict[str, Any]],
        connections: list[dict[str, Any]],
    ) -> bytes | str:
        if output_format == "pdf":
            return build_floor_pdf(
                building=building,
                floor=floor,
                spaces=spaces,
                connections=connections,
            )
        if output_format == "png":
            return build_floor_png(
                building=building,
                floor=floor,
                spaces=spaces,
                connections=connections,
            )
        if output_format == "svg":
            return build_floor_svg(
                building=building,
                floor=floor,
                spaces=spaces,
                connections=connections,
            )
        raise ValueError("Floor plan format must be pdf, png, or svg")
